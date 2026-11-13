"""Protected Core-side IPTV management; intentionally NOT routed by public nginx."""
from __future__ import annotations

import hmac
import logging
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from stremiosrv.iptv import store

router = APIRouter(prefix="/_iptv-admin")
log = logging.getLogger("stremiosrv.iptv")


class PlaylistInput(BaseModel):
    url: str = Field(max_length=4096)


def _admin(request: Request, secret: str) -> None:
    peer = request.client.host if request.client else ""
    from ipaddress import ip_address
    try:
        ip = ip_address(peer)
    except ValueError:
        raise HTTPException(404) from None
    # The management route is only reachable from the internal Docker network.
    if not (ip.is_private or ip.is_loopback):
        raise HTTPException(404)
    try:
        expected = store._config()["signing_secret"]
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, "IPTV unavailable") from None
    if not secret or not hmac.compare_digest(secret, expected):
        raise HTTPException(404)


@router.get("/status")
def status(request: Request, x_iptv_secret: str = Header(default="")) -> dict:
    _admin(request, x_iptv_secret)
    return store.cache_status()


@router.post("/test")
def test_playlist(request: Request, body: PlaylistInput, x_iptv_secret: str = Header(default="")) -> dict:
    _admin(request, x_iptv_secret)
    url = body.url.strip()
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise HTTPException(400, "A playlist tem de utilizar HTTPS e um URL válido.")
    try:
        data = store.parse_m3u(store._download(url))
    except (OSError, ValueError, TimeoutError) as exc:
        log.warning("IPTV candidate validation failed: %s", type(exc).__name__)
        raise HTTPException(422, "Não foi possível validar a playlist M3U.") from None
    if not data:
        raise HTTPException(422, "Playlist sem canais válidos.")
    return {"ok": True, "count": len(data), "sample": [
        {"id": c.id, "name": c.name, "group": c.group}
        for c in list(data.values())[:5]
    ]}


@router.post("/refresh")
def refresh(request: Request, x_iptv_secret: str = Header(default="")) -> dict:
    _admin(request, x_iptv_secret)
    try:
        data = store.channels(force=True)
    except (OSError, ValueError, TimeoutError) as exc:
        log.warning("IPTV refresh failed: %s", type(exc).__name__)
        raise HTTPException(422, "Falha ao actualizar a playlist; versão anterior preservada.") from None
    return {"ok": True, "count": len(data), "refreshedAt": int(time.time())}


@router.get("/channels")
def channels(request: Request, q: str = "", skip: int = 0, limit: int = 50,
             order: str = "number", x_iptv_secret: str = Header(default="")) -> dict:
    _admin(request, x_iptv_secret)
    q = q.strip().casefold()[:100]
    limit = max(1, min(limit, 100))
    skip = max(skip, 0)
    try:
        items = store.channels()
    except (OSError, ValueError, TimeoutError):
        raise HTTPException(503, "Catálogo IPTV indisponível.") from None
    found = [c for c in items.values()
             if not q or q in c.name.casefold() or q in c.group.casefold()]
    prefs = store.preferences()
    ranks = prefs.get("order", {})
    if order == "original":
        found.sort(key=lambda c: c.position)
    elif order == "name":
        found.sort(key=lambda c: (c.name.casefold(), c.position))
    elif order == "group":
        found.sort(key=lambda c: (c.group.casefold(), c.position))
    elif order != "number":
        raise HTTPException(422, "Ordem de canais inválida.")
    return {"total": len(found), "items": [
        {"id": c.id, "name": c.name, "group": c.group,
         "number": c.number, "position": c.position,
         "rank": ranks.get(c.id)} for c in found[skip:skip+limit]
    ]}


@router.get("/hidden")
def hidden(request: Request, x_iptv_secret: str = Header(default="")) -> dict:
    """Restore view for individually hidden channels, with no provider URLs."""
    _admin(request, x_iptv_secret)
    try:
        store.channels()  # ensure the raw catalogue has been loaded
        with store._lock:
            original = list(store._cache["channels"].values())
        excluded = set(store.preferences()["excluded"])
    except (OSError, ValueError, TimeoutError):
        raise HTTPException(503, "Catálogo IPTV indisponível.") from None
    rows = [c for c in original if c.id in excluded]
    rows.sort(key=lambda c: (c.number if c.number is not None
                             else 1000000 + c.position, c.position))
    return {"total": len(rows), "items": [
        {"id": c.id, "name": c.name, "group": c.group,
         "number": c.number, "position": c.position} for c in rows[:10000]
    ]}


@router.get("/playback")
def playback(request: Request, x_iptv_secret: str = Header(default="")) -> dict:
    """Live IPTV viewing state and name/workload correlation, no provider URLs.

    Separate from the torrent PlaybackRegistry: IPTV is an HTTP source and
    has no torrent infohash. Inference of activity relies on recent HLS
    segment requests; a stale FFmpeg process alone is not an active viewer.
    """
    _admin(request, x_iptv_secret)
    from stremiosrv.iptv import switches
    converter = getattr(request.app.state, "converter", None)
    if converter is None:
        return {"active": [], "count": 0}
    try:
        channels = store.channels()
        rows = []
        for activity in switches.active_jobs():
            job = activity["jobId"]
            item = store.resolve_job(job)
            if item is None or item.id not in channels:
                continue
            workload = converter.workload_for_job(job)
            rows.append({
                "id": item.id, "name": item.name, "group": item.group,
                "number": item.number, "position": item.position,
                "viewers": activity["viewers"],
                "idleSeconds": activity["idleSeconds"], "workloadId": workload,
                "delivery": "server-hls",
            })
        rows.sort(key=lambda row: (row["number"] is None,
                                  row["number"] or 0, row["name"].casefold()))
        return {"active": rows, "count": len(rows)}
    except (OSError, ValueError, TimeoutError):
        raise HTTPException(503, "Monitorização IPTV indisponível.") from None
