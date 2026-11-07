"""Thread-safe playback activity registry.

This is deliberately separate from libtorrent's Handle.is_active(): an open source
range can belong to the real player or to FFmpeg reading its input.  The registry
records the HTTP consumer, byte activity, HLS job correlation and lifecycle without
changing the stable /active.json contract.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Callable


@dataclass
class PlaybackSession:
    sessionId: str
    kind: str
    infoHash: str
    fileIdx: int
    client: str
    userAgent: str
    startedAt: float
    lastActivity: float
    bytesServed: int = 0
    openRequests: int = 0
    state: str = "STARTING"
    jobId: str | None = None
    workloadId: str | None = None
    endedAt: float | None = None


class PlaybackRegistry:
    def __init__(self, *, clock: Callable[[], float] = time.time, active_window: float = 15.0,
                 retention: float = 120.0) -> None:
        self._clock = clock
        self._active_window = active_window
        self._retention = retention
        self._lock = threading.RLock()
        self._sessions: dict[str, PlaybackSession] = {}
        self._jobs: dict[str, str] = {}

    @staticmethod
    def classify_client(user_agent: str | None) -> str:
        ua = (user_agent or "").lower()
        if "lavf/" in ua or "ffmpeg" in ua:
            return "ffmpeg"
        if "stremio" in ua:
            return "stremio"
        if "mozilla/" in ua:
            return "browser"
        return "http"

    def open_source(self, info_hash: str, file_idx: int, user_agent: str | None) -> str:
        now = self._clock()
        sid = uuid.uuid4().hex
        with self._lock:
            self._sessions[sid] = PlaybackSession(
                sessionId=sid, kind="source", infoHash=info_hash.lower(), fileIdx=file_idx,
                client=self.classify_client(user_agent), userAgent=user_agent or "",
                startedAt=now, lastActivity=now, openRequests=1, state="BUFFERING",
            )
            self._prune(now)
        return sid

    def note_bytes(self, session_id: str, count: int) -> None:
        now = self._clock()
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.bytesServed += max(0, int(count))
            session.lastActivity = now
            session.state = "PLAYING"

    def close_source(self, session_id: str) -> None:
        now = self._clock()
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.openRequests = 0
            session.lastActivity = now
            # A completed HTTP range is not proof that playback stopped: browsers
            # and native clients commonly consume media as consecutive range requests.
            # Keep a real client recently active for the activity window; FFmpeg reads
            # remain excluded independently by client classification.
            session.endedAt = now
            session.state = "IDLE"

    def register_hls_job(self, job_id: str, info_hash: str, file_idx: int, user_agent: str | None,
                         workload_id: str | None = None) -> str:
        now = self._clock()
        with self._lock:
            sid = self._jobs.get(job_id)
            if sid and sid in self._sessions:
                session = self._sessions[sid]
                session.lastActivity = now
                if workload_id:
                    session.workloadId = workload_id
                return sid
            sid = uuid.uuid4().hex
            self._sessions[sid] = PlaybackSession(
                sessionId=sid, kind="hls", infoHash=info_hash.lower(), fileIdx=file_idx,
                client=self.classify_client(user_agent), userAgent=user_agent or "",
                startedAt=now, lastActivity=now, state="BUFFERING", jobId=job_id,
                workloadId=workload_id,
            )
            self._jobs[job_id] = sid
            self._prune(now)
            return sid

    def touch_hls(self, job_id: str, user_agent: str | None = None) -> None:
        now = self._clock()
        with self._lock:
            sid = self._jobs.get(job_id)
            session = self._sessions.get(sid) if sid else None
            if session is None:
                return
            session.lastActivity = now
            if user_agent:
                session.userAgent = user_agent
                session.client = self.classify_client(user_agent)
            session.state = "PLAYING"

    def end_hls(self, job_id: str) -> None:
        now = self._clock()
        with self._lock:
            sid = self._jobs.pop(job_id, None)
            session = self._sessions.get(sid) if sid else None
            if session:
                session.lastActivity = now
                session.endedAt = now
                session.state = "ENDED"

    def snapshot(self) -> dict[str, object]:
        now = self._clock()
        with self._lock:
            self._prune(now)
            rows = []
            for session in self._sessions.values():
                row = asdict(session)
                recent = now - session.lastActivity <= self._active_window
                # Source reads by FFmpeg are internal pipeline activity, not proof
                # that an end user is currently consuming the title.
                row["active"] = recent and session.state != "ENDED" and session.client != "ffmpeg"
                row["ageSeconds"] = round(now - session.startedAt, 3)
                row["idleSeconds"] = round(now - session.lastActivity, 3)
                rows.append(row)
            rows.sort(key=lambda x: x["lastActivity"], reverse=True)
            return {
                "activeWindowSeconds": self._active_window,
                "sessions": rows,
                "active": [row for row in rows if row["active"]],
            }

    def _prune(self, now: float) -> None:
        stale = [
            sid for sid, session in self._sessions.items()
            if now - session.lastActivity > self._retention
        ]
        for sid in stale:
            job_id = self._sessions[sid].jobId
            self._sessions.pop(sid, None)
            if job_id and self._jobs.get(job_id) == sid:
                self._jobs.pop(job_id, None)
