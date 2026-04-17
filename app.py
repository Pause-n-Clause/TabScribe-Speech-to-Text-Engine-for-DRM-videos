import os, json, time, queue, threading
from typing import List, Dict
import numpy as np
from flask import Flask, render_template, Response, jsonify, request

app = Flask(__name__)

# ── Global state ──────────────────────────────────────────────────────────────
transcript_segments: List[Dict] = []
export_history:      List[Dict] = []   # logged every time user exports
is_recording  = False
is_paused     = False
audio_queue:  queue.Queue = queue.Queue()
update_queue: queue.Queue = queue.Queue()
model         = None
model_ready   = False
recording_start_time = 0.0
status_message = "Loading Whisper model (base)…"

CHUNK_SECONDS = 6
WHISPER_RATE  = 16000

# ── Load Whisper model in background ─────────────────────────────────────────
def _load_model():
    global model, model_ready, status_message
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
        model_ready = True
        status_message = "Ready — click Start to begin transcription"
    except Exception as e:
        status_message = f"Model load error: {e}"

threading.Thread(target=_load_model, daemon=True).start()

# ── Audio capture (WASAPI loopback — speakers only, zero mic) ─────────────────
def _record_system_audio():
    global is_recording, recording_start_time
    import pyaudiowpatch as pyaudio

    p = pyaudio.PyAudio()
    try:
        wasapi    = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers  = p.get_device_info_by_index(wasapi["defaultOutputDevice"])

        if not speakers.get("isLoopbackDevice"):
            for lb in p.get_loopback_device_info_generator():
                if speakers["name"] in lb["name"]:
                    speakers = lb
                    break

        rate          = int(speakers["defaultSampleRate"])
        channels      = int(speakers["maxInputChannels"])
        target_frames = int(rate * CHUNK_SECONDS)
        buf: List[np.ndarray] = []
        recording_start_time  = time.time()

        def _cb(in_data, frame_count, time_info, status):
            chunk = np.frombuffer(in_data, dtype=np.float32).copy()
            if channels > 1:
                chunk = chunk.reshape(-1, channels).mean(axis=1)
            buf.append(chunk)
            if sum(len(x) for x in buf) >= target_frames:
                combined = np.concatenate(buf)[:target_frames]
                buf.clear()
                if rate != WHISPER_RATE:
                    n = int(len(combined) * WHISPER_RATE / rate)
                    combined = np.interp(
                        np.linspace(0, len(combined) - 1, n),
                        np.arange(len(combined)),
                        combined,
                    ).astype(np.float32)
                audio_queue.put(combined)
            return (None, pyaudio.paContinue)

        stream = p.open(
            format=pyaudio.paFloat32,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=int(speakers["index"]),
            frames_per_buffer=1024,
            stream_callback=_cb,
        )
        stream.start_stream()
        while is_recording:
            time.sleep(0.05)
        stream.stop_stream()
        stream.close()
    finally:
        p.terminate()

# ── Whisper transcription worker ──────────────────────────────────────────────
def _transcribe_worker():
    elapsed_offset = 0.0
    while is_recording or not audio_queue.empty():
        try:
            chunk = audio_queue.get(timeout=1.5)
        except queue.Empty:
            continue
        if model is None or is_paused:
            continue
        try:
            segments, _ = model.transcribe(chunk, beam_size=5, vad_filter=True)
            for seg in segments:
                text = seg.text.strip()
                if not text:
                    continue
                entry = {
                    "id":    len(transcript_segments) + 1,
                    "start": round(elapsed_offset + seg.start, 2),
                    "end":   round(elapsed_offset + seg.end,   2),
                    "text":  text,
                }
                transcript_segments.append(entry)
                update_queue.put(entry)
            elapsed_offset += CHUNK_SECONDS
        except Exception as e:
            update_queue.put({"error": str(e)})

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/status")
def status():
    return jsonify({"ready": model_ready, "message": status_message})

@app.route("/start", methods=["POST"])
def start():
    global is_recording, is_paused, transcript_segments
    if not model_ready:
        return jsonify({"error": "Model not ready yet"}), 503
    if is_recording:
        return jsonify({"status": "already_recording"})
    is_recording = True
    is_paused    = False
    transcript_segments = []
    threading.Thread(target=_record_system_audio, daemon=True).start()
    threading.Thread(target=_transcribe_worker,   daemon=True).start()
    return jsonify({"status": "started"})

@app.route("/stop", methods=["POST"])
def stop():
    global is_recording, is_paused
    is_recording = False
    is_paused    = False
    return jsonify({"status": "stopped", "segments": len(transcript_segments)})

@app.route("/stream")
def stream():
    def _generate():
        while True:
            try:
                entry = update_queue.get(timeout=20)
                yield f"data: {json.dumps(entry)}\n\n"
            except queue.Empty:
                yield "data: {\"ping\":true}\n\n"
    return Response(_generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/pause", methods=["POST"])
def pause():
    global is_paused
    data = request.get_json()
    is_paused = data.get("paused", False)
    return jsonify({"paused": is_paused})

@app.route("/export/<fmt>")
def export(fmt: str):
    def _ts(s: float) -> str:
        h, rem = divmod(int(s), 3600)
        m, sec = divmod(rem, 60)
        ms = int((s % 1) * 1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    if fmt == "txt":
        body = "\n\n".join(s["text"] for s in transcript_segments)
        filename = f"transcript_{int(time.time())}.txt"
        # Log to history
        export_history.append({
            "filename":  filename,
            "fmt":       "TXT",
            "segments":  len(transcript_segments),
            "words":     sum(len(s["text"].split()) for s in transcript_segments),
            "saved_at":  time.strftime("%d %b %Y, %H:%M"),
            "timestamp": time.time(),
        })
        return Response(body, mimetype="text/plain",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

    if fmt == "srt":
        lines = []
        for i, s in enumerate(transcript_segments, 1):
            lines.append(f"{i}\n{_ts(s['start'])} --> {_ts(s['end'])}\n{s['text']}\n")
        body = "\n".join(lines)
        filename = f"transcript_{int(time.time())}.srt"
        # Log to history
        export_history.append({
            "filename":  filename,
            "fmt":       "SRT",
            "segments":  len(transcript_segments),
            "words":     sum(len(s["text"].split()) for s in transcript_segments),
            "saved_at":  time.strftime("%d %b %Y, %H:%M"),
            "timestamp": time.time(),
        })
        return Response(body, mimetype="text/plain",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

    return jsonify({"error": "unknown format"}), 400

@app.route("/history")
def history():
    # Return newest first
    return jsonify(list(reversed(export_history)))

if __name__ == "__main__":
    print("\n  🎙  Tabscribe  —  http://127.0.0.1:5000\n")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
