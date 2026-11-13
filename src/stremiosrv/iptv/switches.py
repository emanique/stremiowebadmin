"""Track which signed IPTV channel each LAN client is actually watching.

Avoid leaving old FFmpeg jobs running after channel changes. Do not stop a
shared channel when another client still watches it. Torrent jobs are excluded.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_clients: dict[str, tuple[str, float]] = {}
_TTL = 3600


def switch(client_ip: str, job_id: str, *, now: float | None = None) -> str | None:
    """Return the former IPTV job only if it no longer belongs to any active client."""
    now = time.monotonic() if now is None else now
    with _lock:
        for addr, (_, seen) in list(_clients.items()):
            if now - seen > _TTL:
                _clients.pop(addr, None)
        previous = _clients.get(client_ip, ("", now))[0]
        _clients[client_ip] = (job_id, now)
        if previous and previous != job_id and not any(
            j == previous for j, _ in _clients.values()
        ):
            return previous
        return None


def touch(client_ip: str, job_id: str, *, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    with _lock:
        if _clients.get(client_ip, ("", 0))[0] == job_id:
            _clients[client_ip] = (job_id, now)


def reset() -> None:
    with _lock:
        _clients.clear()


def active_jobs(*, max_idle: float = 20.0, now: float | None = None) -> list[dict]:
    """Summarise recent client HLS segment activity without exposing addresses.

    The IPTV channel id is the only returned identifier. The signed job
    capability and upstream provider URL never leave the Core.
    """
    now = time.monotonic() if now is None else now
    with _lock:
        counts: dict[str, tuple[int, float]] = {}
        for job, seen in _clients.values():
            if now - seen > max_idle:
                continue
            old_count, old_seen = counts.get(job, (0, seen))
            counts[job] = (old_count + 1, max(seen, old_seen))
    return [
        {"jobId": job, "viewers": count, "idleSeconds": round(max(0.0, now - seen), 1)}
        for job, (count, seen) in counts.items()
    ]
