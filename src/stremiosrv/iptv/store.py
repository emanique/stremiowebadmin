"""Bounded M3U source and signed, stable channel identifiers.

The operator-owned playlist credentials are kept on the server's private volume.
A cached channel index is used for all addon responses; no credentials are
embedded in a Stremio addon URL, catalog, or playable HLS URL.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_CONFIG = "/config/iptv-local.json"
_MAX_BYTES = 8 * 1024 * 1024
_EXTINF_ATTR = re.compile(r'([\w-]+)="([^"]*)"')
_NUMBER_PREFIX = re.compile(r"^\s*(\d{1,6})\s*[-.)|:]\s*")
_CHANNEL_ID = re.compile(r"^[a-f0-9]{24}$")
_JOB_RE = re.compile(r"^iptv-([a-f0-9]{24})-([a-f0-9]{24})$")
_lock = threading.RLock()
_cache: dict = {"until": 0.0, "channels": {}, "config_path": "", "config_fingerprint": None,
                "refreshed_at": None, "last_error": None}


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    group: str
    url: str
    number: int | None = None
    position: int = 0


def _config() -> dict:
    path = Path(os.environ.get("STREMIOSRV_IPTV_CONFIG", _CONFIG))
    if not path.is_file() or path.is_symlink():
        raise ValueError("IPTV not configured")
    if path.stat().st_mode & 0o077:
        raise ValueError("IPTV config must be owner-only (chmod 600)")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid IPTV config")
    if data.get("playlist_source") == "file":
        local = Path(os.environ.get("STREMIOSRV_IPTV_UPLOAD", "/config/iptv-playlist-upload.m3u"))
        if not local.is_file() or local.is_symlink() or local.stat().st_size > _MAX_BYTES:
            raise ValueError("private IPTV file is missing or too large")
        if local.stat().st_mode & 0o077:
            raise ValueError("private IPTV file must be owner-only (chmod 600)")
    elif not str(data.get("playlist_url") or "").startswith("https://"):
        raise ValueError("IPTV source must use HTTPS or a private uploaded M3U/M3U8 file")
    if len(data.get("addon_token", "")) < 32 or len(data.get("signing_secret", "")) < 40:
        raise ValueError("IPTV tokens too short")
    data.setdefault("enabled", True)
    return data


def enabled() -> bool:
    try:
        return _config().get("enabled") is not False
    except (ValueError, OSError):
        return False


def check_token(token: str) -> bool:
    try:
        return hmac.compare_digest(token, _config()["addon_token"])
    except (OSError, ValueError, KeyError):
        return False


_HLS_MEDIA_TAGS = frozenset((
    "#EXT-X-TARGETDURATION", "#EXT-X-MEDIA-SEQUENCE",
    "#EXT-X-PLAYLIST-TYPE", "#EXT-X-MAP",
    "#EXT-X-BYTERANGE", "#EXT-X-PART", "#EXT-X-PRELOAD-HINT",
))
_HLS_MASTER_TAGS = frozenset((
    "#EXT-X-STREAM-INF", "#EXT-X-I-FRAME-STREAM-INF",
))


def is_hls_manifest(payload: str) -> bool:
    """Differentiate HLS playlists from IPTV channel lists.

    The harmless #EXT-X-SESSION-DATA metadata often appears in IPTV M3U
    catalogues, including playlists with hundreds of #EXTINF:-1 channels.
    Its presence alone must not disqualify the source.
    """
    tags = {
        line.strip().split(":", 1)[0]
        for line in payload.splitlines()
        if line.lstrip().startswith("#EXT-X-")
    }
    return bool(tags & (_HLS_MEDIA_TAGS | _HLS_MASTER_TAGS))


def parse_m3u(payload: str) -> dict[str, Channel]:
    if not payload.lstrip("\ufeff \r\n").startswith("#EXTM3U"):
        raise ValueError("not an M3U playlist")
    # A .m3u8 URL may contain either a channel catalogue or a single HLS
    # media/master playlist. HLS segment manifests must NOT become TV channels.
    if is_hls_manifest(payload):
        raise ValueError("HLS media/master manifest is not an IPTV channel list")
    channels: dict[str, Channel] = {}
    pending: tuple[str, str, str, int | None] | None = None
    position = 0
    duplicates: dict[str, int] = {}
    for raw in payload.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            info, sep, name = line.partition(",")
            attrs = dict(_EXTINF_ATTR.findall(info))
            group = (attrs.get("group-title") or "Outros").strip()[:120]
            label = (name.strip() if sep else attrs.get("tvg-name", "")).strip()[:200]
            raw_number = next((attrs[k] for k in ("tvg-chno", "channel-number", "tvg-num", "ch-number")
                               if k in attrs and attrs[k].strip().isdigit()), "")
            label_number = _NUMBER_PREFIX.match(label)
            number = int(raw_number) if raw_number else (
                int(label_number.group(1)) if label_number else None
            )
            pending = (attrs.get("tvg-id", "").strip()[:150], label, group, number)
        elif line and not line.startswith("#"):
            if pending is None:
                continue
            parsed = urllib.parse.urlsplit(line)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                pending = None
                continue
            epg_id, label, group, number = pending
            identity = "|".join((epg_id, group.casefold(), label.casefold()))
            count = duplicates.get(identity, 0)
            duplicates[identity] = count + 1
            if count:
                identity += "|" + str(count)
            chan_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
            position += 1
            channels[chan_id] = Channel(chan_id, label or "Canal", group, line, number, position)
            pending = None
    return channels


def preferences() -> dict:
    """Separate non-secret local overrides; never commit this file to Git."""
    path = Path(os.environ.get(
        "STREMIOSRV_IPTV_PREFERENCES", "/config/iptv-channel-preferences.json"))
    try:
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            return {"excluded": [], "order": {}}
        data = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid preferences")
        excluded = data.get("excluded", [])
        order = data.get("order", {})
        if not isinstance(excluded, list) or not isinstance(order, dict):
            raise ValueError("invalid preferences")
        return {
            "excluded": [k for k in excluded if isinstance(k, str) and _CHANNEL_ID.fullmatch(k)][:10000],
            "order": {k: v for k, v in order.items()
                      if isinstance(k, str) and _CHANNEL_ID.fullmatch(k)
                      and type(v) is int and 1 <= v <= 1000000},
        }
    except (OSError, ValueError):
        return {"excluded": [], "order": {}}


def sorted_channels(data: dict[str, Channel], prefs: dict | None = None) -> list[Channel]:
    """Original M3U number wins; manual ranks override, never sort by name by default."""
    prefs = preferences() if prefs is None else prefs
    excluded = set(prefs.get("excluded", []))
    order = prefs.get("order", {})
    return sorted((c for c in data.values() if c.id not in excluded),
                  key=lambda c: (
                      order.get(c.id, c.number if c.number is not None else 1000000 + c.position),
                      c.position,
                  ))


def _download(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "StremioSRV-Local-IPTV/1.0", "Accept": "audio/x-mpegurl,*/*"}
    )
    with urllib.request.urlopen(req, timeout=16) as resp:
        blob = resp.read(_MAX_BYTES + 1)
    if len(blob) > _MAX_BYTES:
        raise ValueError("IPTV playlist exceeds 8 MiB")
    return blob.decode("utf-8-sig", "replace")


def _source(config: dict) -> str:
    """Read only the fixed private upload path or the configured HTTPS playlist."""
    if config.get("playlist_source") == "file":
        path = Path(os.environ.get("STREMIOSRV_IPTV_UPLOAD", "/config/iptv-playlist-upload.m3u"))
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
            raise ValueError("uploaded IPTV playlist unavailable or oversized")
        if path.stat().st_mode & 0o077:
            raise ValueError("uploaded IPTV playlist permissions are not private")
        return path.read_text(encoding="utf-8-sig", errors="replace")
    return _download(config["playlist_url"])


def channels(force: bool = False) -> dict[str, Channel]:
    config = _config()
    now = time.monotonic()
    path = os.environ.get("STREMIOSRV_IPTV_CONFIG", _CONFIG)
    if config.get("playlist_source") == "file":
        upload = Path(os.environ.get("STREMIOSRV_IPTV_UPLOAD", "/config/iptv-playlist-upload.m3u"))
        fp = ("file", upload.stat().st_mtime_ns, upload.stat().st_size)
    else:
        fp = ("url", config["playlist_url"])
    with _lock:
        if (not force and _cache["until"] > now and
                _cache["config_path"] == path and _cache["config_fingerprint"] == fp):
            return {c.id: c for c in sorted_channels(_cache["channels"])}
        if (not force and _cache["channels"] and
                _cache["config_path"] == path and _cache["config_fingerprint"] == fp):
            # Keep an existing good index if the upstream is temporarily unavailable.
            try:
                data = parse_m3u(_source(config))
            except (OSError, ValueError):
                _cache["last_error"] = "Falha ao actualizar a playlist; catálogo anterior preservado."
                _cache["until"] = now + 60
                return {c.id: c for c in sorted_channels(_cache["channels"])}
        else:
            try:
                data = parse_m3u(_source(config))
            except (OSError, ValueError, TimeoutError):
                _cache["last_error"] = "Falha ao carregar a playlist M3U."
                raise
        if not data:
            _cache["last_error"] = "A playlist está vazia."
            raise ValueError("IPTV playlist has no playable channels")
        _cache.update(channels=data, until=now + 1800, config_path=path,
                      config_fingerprint=fp, refreshed_at=int(time.time()), last_error=None)
        return {c.id: c for c in sorted_channels(data)}


def signing_job(channel_id: str) -> str:
    secret = _config()["signing_secret"].encode("utf-8")
    sig = hmac.new(secret, channel_id.encode("ascii"), hashlib.sha256).hexdigest()[:24]
    return f"iptv-{channel_id}-{sig}"


def resolve_job(job_id: str) -> Channel | None:
    match = _JOB_RE.fullmatch(job_id)
    if not match:
        return None
    channel_id = match.group(1)
    if not hmac.compare_digest(signing_job(channel_id), job_id):
        return None
    return channels().get(channel_id)


def cache_status() -> dict:
    with _lock:
        total = len(_cache["channels"])
        active = len(sorted_channels(_cache["channels"]))
        return {"enabled": enabled(), "channels": active,
                "sourceChannels": total, "hiddenChannels": total - active,
                "refreshedAt": _cache["refreshed_at"],
                "lastError": _cache["last_error"],
                "nextRefreshSeconds": max(0, round(_cache["until"]-time.monotonic()))}


def reset_cache() -> None:
    with _lock:
        _cache.update(channels={}, until=0.0, config_path="",
                      config_fingerprint=None, refreshed_at=None, last_error=None)
