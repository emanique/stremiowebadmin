"""Stremio-native IPTV catalog, meta and stream resources.

All media URLs point to the Core HLS endpoint. Stremio clients never receive
the IPTV provider URLs, credentials, or channel-specific upstream headers.
"""
from __future__ import annotations

import os
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request

from stremiosrv.iptv import store
from stremiosrv.library import netguard

router = APIRouter(prefix="/iptv")


def _guard(request: Request, token: str) -> None:
    peer = request.client.host if request.client else ""
    ip = netguard.client_ip(peer, request.headers.get("x-forwarded-for", ""))
    if not netguard.is_allowed(ip) or not store.enabled() or not store.check_token(token):
        raise HTTPException(status_code=404)


def _origin(request: Request) -> str:
    # A explicitly pinned public URL avoids trusting X-Forwarded-Proto from
    # arbitrary clients or Docker bridge gateway addresses. The IPTV HLS URL
    # must use the *same* HTTPS origin as its Stremio manifest.
    pinned = os.environ.get("STREMIOSRV_IPTV_PUBLIC_ORIGIN", "").strip().rstrip("/")
    if pinned:
        parsed = urlsplit(pinned)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query
            or parsed.fragment or parsed.path not in ("", "/")
        ):
            raise HTTPException(503, detail="Invalid configured IPTV public origin")
        return pinned
    peer = request.client.host if request.client else ""
    proto = (
        request.headers.get("x-forwarded-proto") or request.url.scheme
        if netguard._is_loopback(peer) else request.url.scheme
    )
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _channel_meta(channel: store.Channel) -> dict:
    return {
        "id": f"iptv:{channel.id}",
        "type": "tv",
        "name": channel.name,
        "description": channel.group,
        "genres": [channel.group],
    }


@router.get("/{token}/manifest.json")
def manifest(token: str, request: Request) -> dict:
    _guard(request, token)
    return {
        "id": "org.stremiosrv.iptv.local",
        "version": "1.0.0",
        "name": "IPTV via StremioSRV",
        "description": "IPTV protected HLS H.264/AAC via the private Core.",
        "types": ["tv"],
        "catalogs": [{"type": "tv", "id": "channels", "name": "Canais IPTV",
                      "extra": [{"name": "skip", "isRequired": False}]}],
        "resources": [
            {"name": "catalog", "types": ["tv"]},
            {"name": "meta", "types": ["tv"], "idPrefixes": ["iptv:"]},
            {"name": "stream", "types": ["tv"], "idPrefixes": ["iptv:"]},
        ],
        "behaviorHints": {"configurable": False, "configurationRequired": False, "p2p": False},
    }


@router.get("/{token}/catalog/tv/channels.json")
@router.get("/{token}/catalog/tv/channels/{extra}.json")
def catalog(token: str, request: Request, extra: str = "") -> dict:
    _guard(request, token)
    skip = 0
    if extra.startswith("skip="):
        try:
            skip = min(max(0, int(extra[5:])), 1000000)
        except ValueError:
            pass
    # store.channels() already returns the persisted numeric / source ordering.
    # Do not sort alphabetically: Stremio must reflect the WebAdmin channel order.
    rows = list(store.channels().values())
    return {"metas": [_channel_meta(c) for c in rows[skip:skip + 100]]}


@router.get("/{token}/meta/tv/{channel_id}.json")
def meta(token: str, channel_id: str, request: Request) -> dict:
    _guard(request, token)
    item = store.channels().get(channel_id.removeprefix("iptv:"))
    return {"meta": _channel_meta(item) if item else {}}


@router.get("/{token}/stream/tv/{channel_id}.json")
def stream(token: str, channel_id: str, request: Request) -> dict:
    _guard(request, token)
    item = store.channels().get(channel_id.removeprefix("iptv:"))
    if item is None:
        return {"streams": []}
    job_id = store.signing_job(item.id)
    return {"streams": [{
        "title": "Core HLS · H.264/AAC",
        "name": "StremioSRV IPTV",
        "url": f"{_origin(request)}/hlsv2/{quote(job_id)}/master.m3u8",
        "behaviorHints": {"notWebReady": False, "bingeGroup": f"local-iptv:{item.id}"},
    }]}
