"""Demand-only caption sessions: explicit playhead and bounded lifetime.

No library scan. No work from addon discovery. No guessed byte-to-time offset.
Only verified audio packet pieces are read when a torrent is incomplete.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.request

from stremiosrv.ai_subtitles.generator import (
    GenerationUnavailable, json_to_srt, run_openvino_bridge, _stamp,
)
from stremiosrv.subtitle_translator import translate_srt_batch
from stremiosrv.subtitle_translator.local_engine import translate_many, translation_path
from stremiosrv.torrent.picker import pieces_for_range

log = logging.getLogger(__name__)
HASH = re.compile(r"^[a-f0-9]{40}$")
VIDEO = frozenset({".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v", ".ts"})
CHUNK_SECONDS = 30
# Keep the current block and two future blocks prepared while the player
# explicitly renews its lease. No transcriptions continue after the lease.
LOOKAHEAD_BLOCKS = 2
LEASE_SECONDS = 25
SEEK_THRESHOLD_SECONDS = 40
RETRY_BACKOFF_SECONDS = 45
MAX_BLOCK_FAILURES = 2


def _audio_available(handle, media: Path, file_index: int, position: int,
                     audio_index: int = 0) -> bool:
    """Require verified pieces for every probed audio packet before invoking FFmpeg.

    File size and sparse allocation alone are not evidence of completed pieces.
    If ffprobe cannot identify packet byte positions, defer rather than read holes.
    """
    if handle.file_complete(file_index):
        return True
    try:
        offset = int(handle.file_offset(file_index))
        size = int(handle.file_size(file_index))
        proc = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", f"a:{audio_index}",
            "-read_intervals", f"{position}%+{CHUNK_SECONDS}",
            "-show_entries", "packet=pos,size,pts_time",
            "-of", "json", str(media),
        ], capture_output=True, text=True, timeout=18, check=True)
        packets = json.loads(proc.stdout).get("packets", [])
        timestamps = []
        for packet in packets:
            pos = int(packet["pos"])
            length = int(packet["size"])
            seconds = float(packet["pts_time"])
            if length <= 0 or pos < 0 or pos + length > size:
                return False
            if not all(handle.have_piece(i) for i in
                       pieces_for_range(offset + pos, offset + pos + length - 1,
                                        handle.piece_length())):
                return False
            timestamps.append(seconds)
        return (len(timestamps) >= 3 and min(timestamps) <= position + 4
                and max(timestamps) >= position + CHUNK_SECONDS - 4)
    except (OSError, KeyError, ValueError, TypeError, subprocess.SubprocessError):
        return False


def _absolute_srt(relative: str, position: int) -> str:
    """Shift local SRT cue timestamps to absolute media time."""
    pattern = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")
    def shift(match):
        h, m, s, ms = map(int, match.groups())
        return _stamp((((h * 60 + m) * 60 + s) * 1000 + ms)
                      + position * 1000)
    return pattern.sub(shift, relative)


class DemandSubtitleSessions:
    def __init__(self, engine, cache_root: str, *, clock=time.monotonic):
        self.engine = engine
        self.root = Path(cache_root).resolve()
        self.clock = clock
        self._lock = threading.RLock()
        self._sessions: dict[str, dict] = {}
        # One retry budget per cached media/audio/block, not per player.
        # Prevents parallel clients from hammering a failing OpenVINO job.
        self._failed_chunks: dict[tuple[str, int, int, int], tuple[int, float]] = {}
        self._recent_client_events: list[dict] = []
        self._running: str | None = None
        self._running_block: int | None = None
        self._process: subprocess.Popen | None = None
        self._bridge_job: str | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-demand")
        self._shutdown = threading.Event()
        self._watch = threading.Thread(target=self._watchdog, name="ai-demand-watchdog",
                                       daemon=True)
        self._watch.start()

    def _assert_media(self, infohash: str, index: int):
        if not HASH.fullmatch(infohash) or type(index) is not int or index < 0:
            raise ValueError("invalid torrent file identity")
        handle = self.engine.get(infohash) if self.engine else None
        if handle is None:
            # A fully downloaded title may be unloaded from libtorrent while
            # remaining in the server cache. Reuse the strict fastresume proof,
            # not the presence/size of a sparse file.
            from stremiosrv.ai_subtitles.resume_scan import complete_cached_files
            for checked_hash, checked_index, media, _ in complete_cached_files(
                    self.root, set(), limit=1000):
                if checked_hash == infohash and checked_index == index:
                    return None, media
            raise GenerationUnavailable("torrent has no active handle or verified complete cache")
        if not handle.has_metadata() or index >= handle.num_files():
            raise GenerationUnavailable("torrent metadata is not currently available")
        relative = Path(handle.file_path(index))
        if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() not in VIDEO:
            raise GenerationUnavailable("unsafe or unsupported video file")
        source = self.root / relative
        if source.is_symlink() or not source.resolve().is_relative_to(self.root):
            raise GenerationUnavailable("unsafe video path")
        if not source.is_file() or source.stat().st_size == 0:
            raise GenerationUnavailable("video file is not currently cached")
        return handle, source

    @staticmethod
    def _duration_seconds(media: Path) -> float | None:
        """Probe the real stream; unknown duration must not be invented."""
        try:
            proc = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
                capture_output=True, text=True, check=True, timeout=8)
            result = float(proc.stdout.strip())
            if math.isfinite(result) and result > 0:
                return result
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return None

    def start(self, infohash: str, file_index: int, position: float,
              audio_index: int = 0):
        if not math.isfinite(position) or position < 0 or position > 24 * 3600:
            raise ValueError("invalid playback position")
        if type(audio_index) is not int or not 0 <= audio_index <= 20:
            raise ValueError("invalid audio track index")
        _, media = self._assert_media(infohash, file_index)
        # Bound the scheduler by the actual movie duration. The native Stremio
        # renderer reports MILLISECONDS; accidentally passing it as seconds
        # otherwise creates thousands of chunks outside the file's timeline.
        duration = self._duration_seconds(media)
        if duration is not None and position > duration + 5:
            raise ValueError("playback position exceeds media duration")
        with self._lock:
            # Every player has its own lease and playhead. The GPU worker is
            # still shared, but selecting AI on another player must NOT stop
            # previously active sessions.
            sid = secrets.token_urlsafe(24)
            self._sessions[sid] = dict(
                sessionId=sid, infohash=infohash, fileIndex=file_index,
                audioIndex=audio_index, positionSeconds=float(position), lastSeen=self.clock(),
                durationSeconds=duration,
                stage="preparing", lastBlock=None, error=None,
                lookaheadSeconds=LOOKAHEAD_BLOCKS * CHUNK_SECONDS,
            )
        # Upgrade existing chunk caches to one cumulative SRT without GPU work.
        with self._lock:
            existing_folder = self._folder(self._sessions[sid])
            if existing_folder.is_dir() and any(existing_folder.glob("chunk-*.srt")):
                self._publish_progressive(existing_folder)
        self._schedule(sid)
        return self.status(sid)

    def _stop_locked(self, sid: str):
        self._sessions.pop(sid, None)
        if self._running == sid:
            if self._process and self._process.poll() is None:
                self._process.terminate()
            if self._bridge_job:
                self._cancel_bridge(self._bridge_job)

    @staticmethod
    def _cancel_bridge(job: str) -> None:
        endpoint = os.getenv("STREMIOSRV_AI_OPENVINO_URL", "").rstrip("/")
        token = os.getenv("STREMIOSRV_AI_OPENVINO_TOKEN", "")
        if not endpoint.startswith("http://ai-openvino:19098") or not token:
            return
        try:
            request = urllib.request.Request(
                endpoint + "/cancel/" + job, data=b"{}",
                headers={"X-AI-Token": token}, method="POST")
            urllib.request.urlopen(request, timeout=2).close()
        except OSError:
            log.warning("OpenVINO job cancellation acknowledgement unavailable")

    def stop(self, sid: str):
        with self._lock:
            self._stop_locked(sid)

    def heartbeat(self, sid: str, position: float):
        if not math.isfinite(position) or position < 0 or position > 24 * 3600:
            raise ValueError("invalid playback position")
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                raise KeyError("session expired")
            duration = session.get("durationSeconds")
            if duration is not None and position > duration + 5:
                self._stop_locked(sid)
                raise ValueError("playback position exceeds media duration")
            previous = session["positionSeconds"]
            session["positionSeconds"] = float(position)
            session["lastSeen"] = self.clock()
            # Normal 1x playback is *not* a seek: never cancel when the
            # playhead crosses a 30-second boundary. The GPU must stay ahead.
            if abs(position - previous) >= SEEK_THRESHOLD_SECONDS:
                session["stage"] = "preparing"
                session["error"] = None
                if self._running == sid and self._running_block is not None:
                    start = int(position // CHUNK_SECONDS) * CHUNK_SECONDS
                    if not (start <= self._running_block <=
                            start + LOOKAHEAD_BLOCKS * CHUNK_SECONDS):
                        session["seekPending"] = True
                        if self._process and self._process.poll() is None:
                            self._process.terminate()
                        if self._bridge_job:
                            self._cancel_bridge(self._bridge_job)
        self._schedule(sid)
        return self.status(sid)

    def _folder(self, row: dict) -> Path:
        # AI to PT always runs audio -> Whisper -> local language translation.
        # Existing subtitle tracks belong exclusively to Subtitle Translator.
        return (self.root / ".ai-subtitles" / row["infohash"] / "on-demand"
                / str(row["fileIndex"]) / str(row["audioIndex"]))

    @staticmethod
    def _block_ready(folder: Path, position: int) -> bool:
        file = folder / f"chunk-{position:08}.srt"
        return file.is_file() and not file.is_symlink() and 0 < file.stat().st_size < 150000

    @staticmethod
    def _chunk_key(row: dict, position: int) -> tuple[str, int, int, int]:
        return (row["infohash"], row["fileIndex"], row["audioIndex"], position)

    def _note_chunk_failure(self, row: dict, position: int) -> int:
        """Share bounded retry counts across simultaneous player sessions."""
        key = self._chunk_key(row, position)
        previous, _ = self._failed_chunks.get(key, (0, 0.0))
        attempts = previous + 1
        self._failed_chunks[key] = (attempts, self.clock() + RETRY_BACKOFF_SECONDS)
        # Bounded diagnostic history even after many seek/new-title operations.
        while len(self._failed_chunks) > 512:
            self._failed_chunks.pop(next(iter(self._failed_chunks)))
        return attempts

    def _pending_block(self, row: dict) -> int | None:
        start = int(row["positionSeconds"] // CHUNK_SECONDS) * CHUNK_SECONDS
        folder = self._folder(row)
        for step in range(LOOKAHEAD_BLOCKS + 1):
            block = start + step * CHUNK_SECONDS
            if self._block_ready(folder, block):
                continue
            # Prevent repeated GPU 500s when two players request one block.
            failures, retry_at = self._failed_chunks.get(
                self._chunk_key(row, block), (0, 0.0))
            if failures >= MAX_BLOCK_FAILURES or self.clock() < retry_at:
                return None
            return block
        return None

    @staticmethod
    def _publish_progressive(folder: Path) -> Path:
        """Atomic cumulative SRT for the same movie/file/audio track.

        Chunk cache remains reusable, but consumers always read one stable
        progressive.srt filename containing all successful chunks in order.
        """
        target = folder / "progressive.srt"
        cues = []
        for source in sorted(folder.glob("chunk-*.srt")):
            if source.is_symlink() or source.stat().st_size > 150000:
                continue
            for item in re.split(r"\n\s*\n", source.read_text("utf8").strip()):
                rows = item.splitlines()
                if len(rows) < 3:
                    continue
                time_line = next((i for i, line in enumerate(rows[:3]) if "-->" in line), None)
                if time_line is None:
                    continue
                stamp = rows[time_line].strip()
                content = "\n".join(rows[time_line+1:]).strip()
                if content:
                    cues.append((stamp.split(" --> ")[0], stamp, content))
        cues.sort(key=lambda x: (x[0], x[1]))
        # Preserve sequence numbering and remove duplicate adjacent captions.
        merged = []
        for _, stamp, content in cues:
            if merged and merged[-1] == (stamp, content):
                continue
            merged.append((stamp, content))
        text = "\n\n".join(f"{n}\n{stamp}\n{content}"
                          for n, (stamp, content) in enumerate(merged, 1)) + "\n"
        fd, stage = tempfile.mkstemp(prefix=".progressive-", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(stage, target)
        finally:
            if os.path.exists(stage):
                os.unlink(stage)
        return target

    def session_srt(self, sid: str) -> str:
        """A session-specific snapshot of *shared* cached Whisper chunks.

        A new sessionId gives each player an independent URL and playhead.
        Never start work from a subtitle GET; the companion client must use
        explicit start/heartbeat/stop requests and refresh this URL.
        """
        with self._lock:
            row = self._sessions.get(sid)
            if row is None:
                raise KeyError("session expired")
            start = int(row["positionSeconds"] // CHUNK_SECONDS) * CHUNK_SECONDS
            folder = self._folder(row)
            blocks = range(max(0, start - CHUNK_SECONDS),
                           start + (LOOKAHEAD_BLOCKS + 1) * CHUNK_SECONDS,
                           CHUNK_SECONDS)
            cues = []
            for position in blocks:
                source = folder / f"chunk-{position:08}.srt"
                if not self._block_ready(folder, position):
                    continue
                for segment in re.split(r"\n\s*\n", source.read_text("utf8").strip()):
                    lines = segment.splitlines()
                    stamp_index = next((i for i, line in enumerate(lines[:3])
                                        if " --> " in line), None)
                    if stamp_index is not None:
                        content = "\n".join(lines[stamp_index + 1:]).strip()
                        if content:
                            cues.append((lines[stamp_index], content))
            if cues:
                return "\n\n".join(
                    f"{i}\n{stamp}\n{text}"
                    for i, (stamp, text) in enumerate(cues, 1)) + "\n"
            from stremiosrv.ai_subtitles.generator import _stamp
            start_ms = max(0, int(row["positionSeconds"] * 1000))
            return (f"1\n{_stamp(start_ms)} --> {_stamp(start_ms + 8000)}\n"
                    "A preparar legendas AI em português…\n")

    def status(self, sid: str) -> dict:
        with self._lock:
            row = self._sessions.get(sid)
            if row is None:
                raise KeyError("session expired")
            result = {key: value for key, value in row.items()
                      if key not in ("lastSeen", "seekPending")}
            first = int(row["positionSeconds"] // CHUNK_SECONDS) * CHUNK_SECONDS
            folder = self._folder(row)
            result["currentBlockReady"] = self._block_ready(folder, first)
            buffered = first
            for step in range(LOOKAHEAD_BLOCKS + 1):
                block = first + step * CHUNK_SECONDS
                if not self._block_ready(folder, block):
                    break
                buffered = block + CHUNK_SECONDS
            result["bufferedUntilSeconds"] = buffered
            return result

    def record_client_event(self, event: str, http_status: int = 0) -> None:
        # Only controlled event names and HTTP status codes, never URLs,
        # manifest tokens, media titles, user identifiers or subtitles.
        allowed = {"selected", "started", "start_failed", "heartbeat_failed",
                   "srt_received", "stopped", "player_not_ready"}
        if event not in allowed or type(http_status) is not int or not 0 <= http_status <= 599:
            raise ValueError("invalid playback diagnostic event")
        with self._lock:
            self._recent_client_events.append({
                "event": event, "httpStatus": http_status,
                "secondsAgo": 0, "_observedAt": self.clock(),
            })
            del self._recent_client_events[:-20]

    def snapshot(self):
        with self._lock:
            now = self.clock()
            events = [{"event": x["event"], "httpStatus": x["httpStatus"],
                       "secondsAgo": round(max(0, now - x["_observedAt"]), 1)}
                      for x in self._recent_client_events]
            return {"mode": "on_demand", "automaticScan": False,
                    "active": [self.status(sid) for sid in self._sessions],
                    "recentClientEvents": events,
                    "heartbeatTimeoutSeconds": LEASE_SECONDS}

    def _schedule(self, sid):
        with self._lock:
            row = self._sessions.get(sid)
            if row is None or self._running is not None:
                return
            target = self._pending_block(row)
            if target is None:
                return
            # A previous failure/buffering state is only retried on the next
            # heartbeat, not through a busy-spin in the worker completion.
            self._running = sid
            self._running_block = target
        self._pool.submit(self._process_next, sid, target)

    def _watchdog(self):
        while not self._shutdown.wait(2):
            with self._lock:
                now = self.clock()
                for sid, row in list(self._sessions.items()):
                    if now - row["lastSeen"] > LEASE_SECONDS:
                        self._stop_locked(sid)

    def _active(self, sid):
        with self._lock:
            row = self._sessions.get(sid)
            if row is None or self.clock() - row["lastSeen"] > LEASE_SECONDS:
                return None
            return dict(row)

    def _set(self, sid, **values):
        with self._lock:
            if sid in self._sessions:
                self._sessions[sid].update(values)

    def _process_next(self, sid, position: int):
        succeeded = False
        try:
            session = self._active(sid)
            if not session:
                return
            index, infohash = session["fileIndex"], session["infohash"]
            audio_index = session["audioIndex"]
            folder = self._folder(session)
            result = folder / f"chunk-{position:08}.srt"
            if self._block_ready(folder, position):
                succeeded = True
                self._set(sid, stage="ready", lastBlock=position)
                return
            handle, media = self._assert_media(infohash, index)
            original_stat = media.stat()
            if handle is not None and not _audio_available(handle, media, index, position, audio_index):
                self._set(sid, stage="buffering_audio", error="audio bytes not yet verified")
                return
            with tempfile.TemporaryDirectory(prefix="ai-subtitles-", dir=os.getenv(
                    "STREMIOSRV_AI_OPENVINO_WORK_DIR", "/opt/ai-work")) as td:
                scratch = Path(td)
                command = ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(position),
                           "-i", str(media), "-t", str(CHUNK_SECONDS),
                           "-map", f"0:a:{audio_index}", "-vn", "-ac", "1", "-ar", "16000",
                           "-c:a", "pcm_s16le", str(scratch / "audio.wav")]
                self._set(sid, stage="extracting_audio")
                with subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) as proc:
                    with self._lock:
                        self._process = proc
                    try:
                        _, error = proc.communicate(timeout=80)
                    finally:
                        with self._lock:
                            self._process = None
                    if proc.returncode:
                        raise GenerationUnavailable("audio chunk unavailable: " +
                                                    error.decode(errors="replace")[-140:])
                if not self._active(sid):
                    return
                with self._lock:
                    self._bridge_job = scratch.name
                self._set(sid, stage="transcribing")
                run_openvino_bridge(scratch, os.getenv("STREMIOSRV_AI_OPENVINO_URL", ""),
                                    os.getenv("STREMIOSRV_AI_OPENVINO_TOKEN", ""))
                if not self._active(sid):
                    return
                raw, language = json_to_srt(json.loads(
                    (scratch / "whisper.json").read_text("utf8")), allow_silence=True)
                if language not in ("en", "fr", "es", "pt", "pt-pt"):
                    raise GenerationUnavailable("unsupported detected audio language: " + language)
                if raw and language not in ("pt", "pt-pt"):
                    if not translation_path(language, "pt"):
                        raise GenerationUnavailable("local translation unavailable: " + language)
                    self._set(sid, stage="translating")
                    raw = translate_srt_batch(
                        raw, lambda lines: translate_many(lines, language, "pt-PT"))
                if not self._active(sid):
                    return
                # A single newline is a valid, intentionally empty cached
                # block: the scheduler can advance, while SRT clients see no
                # subtitle cue. Do not emit a fabricated silence caption.
                text = _absolute_srt(raw, position) if raw else "\n"
                from stremiosrv import cache
                with cache._TITLE_PURGE_LOCK:
                    if not self._active(sid):
                        return
                    # Publish a prefetched chunk if it still belongs to the
                    # current rolling window. Crossing a normal chunk boundary
                    # must not discard completed work.
                    current = self._active(sid)
                    if current is None:
                        return
                    current_start = (int(current["positionSeconds"] // CHUNK_SECONDS)
                                     * CHUNK_SECONDS)
                    if not (current_start - CHUNK_SECONDS <= position <=
                            current_start + LOOKAHEAD_BLOCKS * CHUNK_SECONDS):
                        return
                    # A title may have been deleted while OpenVINO was running.
                    # Never recreate subtitles for an evicted/replaced media file.
                    active_handle, live_media = self._assert_media(infohash, index)
                    new_stat = live_media.stat()
                    if (live_media != media or new_stat.st_ino != original_stat.st_ino
                            or new_stat.st_dev != original_stat.st_dev
                            or new_stat.st_size != original_stat.st_size):
                        return
                    folder.mkdir(parents=True, exist_ok=True)
                    tmp = folder / ("." + secrets.token_hex(12) + ".tmp")
                    try:
                        tmp.write_text(text, "utf8")
                        os.replace(tmp, result)
                        self._publish_progressive(folder)
                    finally:
                        tmp.unlink(missing_ok=True)
                succeeded = True
                with self._lock:
                    row = self._sessions.get(sid)
                    if row:
                        self._failed_chunks.pop(self._chunk_key(row, position), None)
                self._set(sid, stage="ready", lastBlock=position, error=None)
        except (OSError, ValueError, GenerationUnavailable, subprocess.SubprocessError) as exc:
            with self._lock:
                row = self._sessions.get(sid)
                # A client stopping or seeking can cancel GPU work. Treat
                # that as cancellation, not an OpenVINO model failure.
                cancelled = row is None or bool(row.get("seekPending"))
                attempts = (self._note_chunk_failure(row, position)
                            if not cancelled else 0)
            if not cancelled:
                self._set(sid, stage="error", failedBlock=position,
                          error=str(exc)[:160])
                log.warning("Demand subtitles block %d failed (shared retry %d/%d): %s",
                            position, attempts, MAX_BLOCK_FAILURES, exc)
        finally:
            with self._lock:
                self._running = None
                self._running_block = None
                self._bridge_job = None
                pending = []
                for key, record in self._sessions.items():
                    if record.pop("seekPending", False):
                        record["stage"] = "preparing"
                        pending.append(key)
                # A successful block should serve another waiting player
                # before this client claims a further lookahead block. Keep
                # one GPU job at a time and share already completed chunks.
                for candidate in self._sessions:
                    if candidate != sid and candidate not in pending:
                        pending.append(candidate)
                if succeeded and sid in self._sessions and sid not in pending:
                    pending.append(sid)
            for candidate in pending:
                with self._lock:
                    if self._running is not None:
                        break
                self._schedule(candidate)

    def close(self):
        self._shutdown.set()
        with self._lock:
            for sid in list(self._sessions):
                self._stop_locked(sid)
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._watch.join(timeout=3)
