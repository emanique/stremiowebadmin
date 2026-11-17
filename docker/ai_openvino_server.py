"""Internal-only, single-job OpenVINO Whisper bridge for the Stremio Core.

No public ports, no media access, no shell interpolation, and no model downloads.
A shared scratch volume exchanges one 16 kHz WAV and one JSON output.
"""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(os.getenv("AI_WORK_DIR", "/opt/ai-work")).resolve()
MODEL = Path(os.getenv("AI_MODEL", "/models/ggml-small.bin"))
TOKEN = os.environ["AI_BRIDGE_TOKEN"]
BINARY = "/src/whisper.cpp/build/bin/whisper-cli"
JOB = re.compile(r"^ai-subtitles-[a-zA-Z0-9_-]{6,80}$")
PROGRESS = re.compile(r"progress\s*=\s*(\d{1,3})%", re.I)
lock = threading.Lock()
process_lock = threading.Lock()
active_process = {"job": None, "process": None}
current = {"job": None, "percent": 0, "stage": "idle", "error": ""}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def respond(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        import hmac
        return hmac.compare_digest(self.headers.get("X-AI-Token", ""), TOKEN)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            import openvino as ov
            devices = ov.Core().available_devices
            ok = "GPU" in devices and MODEL.is_file()
            return self.respond(200 if ok else 503, {"ready": ok, "gpu": "GPU" in devices})
        if not self.authorized():
            return self.respond(403, {"error": "forbidden"})
        if path.startswith("/status/"):
            job = path[len("/status/"):]
            if not JOB.fullmatch(job):
                return self.respond(404, {})
            return self.respond(200, dict(current) if current["job"] == job else {"stage": "unknown"})
        return self.respond(404, {})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path.startswith("/cancel/"):
            if not self.authorized():
                return self.respond(403, {"error": "forbidden"})
            job_id = path.removeprefix("/cancel/")
            if not JOB.fullmatch(job_id):
                return self.respond(404, {})
            with process_lock:
                process = active_process["process"] if active_process["job"] == job_id else None
                if process is not None and process.poll() is None:
                    process.terminate()
                    current["stage"] = "cancelled"
                    return self.respond(200, {"cancelled": True})
            return self.respond(200, {"cancelled": False})
        if path != "/run":
            return self.respond(404, {})
        if not self.authorized():
            return self.respond(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 2048:
                raise ValueError("invalid request length")
            request = json.loads(self.rfile.read(length))
            job = request.get("job")
            language = request.get("language", "auto")
            if not isinstance(job, str) or not JOB.fullmatch(job):
                raise ValueError("invalid job identifier")
            if not isinstance(language, str) or not re.fullmatch(r"(?:auto|[a-z]{2,3})", language):
                raise ValueError("invalid source language")
        except (ValueError, TypeError, json.JSONDecodeError):
            return self.respond(400, {"error": "invalid request"})
        if not lock.acquire(blocking=False):
            return self.respond(409, {"error": "gpu busy"})
        try:
            folder = ROOT / job
            wav = folder / "audio.wav"
            if folder.is_symlink() or wav.is_symlink() or not wav.is_file():
                return self.respond(404, {"error": "audio not found"})
            if not folder.resolve().is_relative_to(ROOT):
                return self.respond(400, {"error": "invalid path"})
            if not 44 <= wav.stat().st_size <= 768 * 1024 * 1024:
                return self.respond(400, {"error": "invalid audio size"})
            output = folder / "whisper"
            current.update(job=job, percent=0, stage="transcribing", error="")
            cmd = [BINARY, "-m", str(MODEL), "-f", str(wav), "-l", language,
                   "-t", "3", "-oved", "GPU", "-oj", "-of", str(output), "-pp", "-np"]
            started = time.monotonic()
            try:
                with subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                      text=True, errors="replace", bufsize=1) as process:
                    with process_lock:
                        active_process["job"], active_process["process"] = job, process
                    lines = []
                    def consume():
                        for line in process.stderr:
                            m = PROGRESS.search(line)
                            if m:
                                current["percent"] = max(current["percent"], min(99, int(m.group(1))))
                            elif "error" in line.lower() or "failed" in line.lower():
                                lines.append(line[-180:].strip())
                                del lines[:-5]
                    reader = threading.Thread(target=consume, daemon=True)
                    reader.start()
                    try:
                        result = process.wait(timeout=4 * 3600)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        raise RuntimeError("OpenVINO transcription timed out")
                    reader.join(timeout=5)
                    with process_lock:
                        active_process["job"], active_process["process"] = None, None
                    if result:
                        raise RuntimeError("OpenVINO inference failed: " + " | ".join(lines)[:220])
                target = output.with_suffix(".json")
                if not target.is_file() or target.is_symlink() or target.stat().st_size > 8 * 1024 * 1024:
                    raise RuntimeError("Whisper output JSON missing")
                current["stage"] = "complete"
                current["percent"] = 100
                return self.respond(200, {"ok": True, "elapsedSeconds": round(time.monotonic()-started, 2)})
            except (OSError, RuntimeError) as exc:
                current.update(stage="error", error=str(exc)[:180])
                return self.respond(500, {"error": str(exc)[:180]})
        finally:
            with process_lock:
                active_process["job"], active_process["process"] = None, None
            lock.release()


if __name__ == "__main__":
    if len(TOKEN) < 32:
        raise SystemExit("AI bridge token must be at least 32 characters")
    if not ROOT.is_dir():
        raise SystemExit("Shared AI scratch volume is not mounted")
    ThreadingHTTPServer(("0.0.0.0", 19098), Handler).serve_forever()
