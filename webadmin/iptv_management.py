"""WebAdmin IPTV management. Config is persisted atomically; Core never restarts."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import config_reliability as previous
import httpx
from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

app = previous.app
IPTV_CONFIG = Path(os.environ.get("IPTV_CONFIG_FILE", "/config/iptv-local.json"))
IPTV_PREFERENCES = Path(os.environ.get("IPTV_PREFERENCES_FILE", "/config/iptv-channel-preferences.json"))
IPTV_UPLOAD = Path(os.environ.get("IPTV_UPLOAD_FILE", "/config/iptv-playlist-upload.m3u"))
CHANNEL_ID = re.compile(r"^[a-f0-9]{24}$")
CORE = os.environ.get("STREMIO_URL", "http://stremio-libtorrent-server:11470").rstrip("/")
_lock = threading.RLock()
_last = {"operation": None, "at": None, "error": None}
log = logging.getLogger("webadmin.iptv")


class ChannelChange(BaseModel):
    action: str = Field(pattern=r"^(hide|restore|rank|clear_rank)$")
    channel_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    rank: int | None = Field(default=None, ge=1, le=1000000)


def _channel_prefs() -> dict:
    """Non-secret user overrides, stored outside the IPTV provider's M3U file."""
    try:
        if IPTV_PREFERENCES.is_symlink() or IPTV_PREFERENCES.stat().st_size > 1024 * 1024:
            raise ValueError("invalid preferences path")
        obj = json.loads(IPTV_PREFERENCES.read_text(encoding="utf-8"))
        if not isinstance(obj, dict) or not isinstance(obj.get("excluded", []), list) or not isinstance(obj.get("order", {}), dict):
            raise ValueError("invalid preferences")
        return {"excluded": [k for k in obj.get("excluded", []) if isinstance(k, str) and CHANNEL_ID.fullmatch(k)],
                "order": {k: v for k, v in obj.get("order", {}).items()
                          if isinstance(k, str) and CHANNEL_ID.fullmatch(k) and type(v) is int and 1 <= v <= 1000000}}
    except FileNotFoundError:
        return {"excluded": [], "order": {}}
    except (ValueError, OSError):
        raise HTTPException(503, "Preferências IPTV inválidas; nenhuma alteração aplicada.") from None


def _write_prefs(data: dict) -> None:
    IPTV_PREFERENCES.parent.mkdir(parents=True, exist_ok=True)
    if IPTV_PREFERENCES.is_symlink():
        raise HTTPException(503, "Destino de preferências IPTV inválido.")
    fd, temporary = tempfile.mkstemp(prefix=".iptv-prefs-", dir=IPTV_PREFERENCES.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            os.fchmod(file.fileno(), 0o600)
            json.dump(data, file, separators=(",", ":"), ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, IPTV_PREFERENCES)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class UploadedPlaylist(BaseModel):
    name: str = Field(min_length=5, max_length=160)
    text: str = Field(min_length=1, max_length=8 * 1024 * 1024)


class Candidate(BaseModel):
    url: str = Field(min_length=1, max_length=4096)


class Update(BaseModel):
    url: str | None = Field(default=None, max_length=4096)
    enabled: bool | None = None


def _load() -> dict:
    try:
        if IPTV_CONFIG.is_symlink():
            raise ValueError("IPTV configuration path cannot be a symlink")
        if not IPTV_CONFIG.exists():
            # Fresh installs do not ship provider usernames, passwords,
            # playlists, addon tokens or signing secrets in the repository.
            _write_atomic({
                "playlist_url": "", "enabled": False,
                "addon_token": secrets.token_urlsafe(40),
                "signing_secret": secrets.token_hex(32),
            })
        if not IPTV_CONFIG.is_file():
            raise ValueError("IPTV configuration unavailable")
        cfg = json.loads(IPTV_CONFIG.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict) or not cfg.get("signing_secret") or not cfg.get("addon_token"):
            raise ValueError("IPTV configuration invalid")
        return cfg
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Configuração IPTV indisponível.") from exc


def _internal(path: str, cfg: dict, data: dict | None = None) -> dict:
    headers = {"X-IPTV-Secret": cfg["signing_secret"]}
    try:
        with httpx.Client(timeout=32.0) as client:
            if data is None:
                response = client.get(CORE + "/_iptv-admin/" + path, headers=headers)
            else:
                response = client.post(CORE + "/_iptv-admin/" + path, headers=headers, json=data)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        # 422 indicates invalid playlist content, not a connectivity problem.
        # Do not expose Core response bodies, provider URLs or tokens.
        code = exc.response.status_code
        log.warning("IPTV Core rejected request: %s", code)
        if code == 422:
            raise HTTPException(422, "Playlist rejeitada pelo Core: verifique se é uma lista IPTV com canais válidos.") from None
        if code in (400, 404):
            raise HTTPException(400, "Pedido IPTV rejeitado pelo Core.") from None
        if code == 503:
            raise HTTPException(503, "O Core IPTV ainda não tem uma lista disponível.") from None
        raise HTTPException(502, "Falha de comunicação ou de processamento no Core IPTV.") from None
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("IPTV Core operation failed: %s", type(exc).__name__)
        raise HTTPException(502, "Não foi possível concluir a operação no Core IPTV.") from None


def _write_atomic(cfg: dict) -> None:
    IPTV_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".iptv-local-", dir=str(IPTV_CONFIG.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(cfg, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, IPTV_CONFIG)
        os.chmod(IPTV_CONFIG, 0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _record(operation: str, error: str | None = None) -> None:
    _last.update(operation=operation, at=datetime.now(UTC).isoformat(), error=error)


def _public_config(cfg: dict) -> dict:
    u = urlsplit(cfg.get("playlist_url", ""))
    # Do not send IPTV access tokens, username, password, path or query to browsers.
    uploaded = cfg.get("playlist_source") == "file"
    return {"configured": bool(u.hostname) or (uploaded and IPTV_UPLOAD.is_file()),
            "sourceHost": "Ficheiro local protegido" if uploaded else u.hostname,
            "sourceType": "file" if uploaded else "url",
            "enabled": cfg.get("enabled", True) is not False}


@app.get("/api/iptv")
def iptv_status() -> dict:
    cfg = _load()
    if not cfg.get("playlist_url") and cfg.get("playlist_source") != "file":
        return {**_public_config(cfg), "channels": 0, "refreshedAt": None,
                "nextRefreshSeconds": 0, "lastError": None,
                "lastOperation": dict(_last)}
    details = _internal("status", cfg)
    return {**_public_config(cfg), **details, "lastOperation": dict(_last)}


@app.post("/api/iptv/test")
def iptv_test(body: Candidate) -> dict:
    # Core authenticates admin requests using the private configured secret.
    # For a fresh install only, stage the candidate temporarily so Core can
    # authenticate the test; restore the empty configuration afterwards.
    with _lock:
        cfg = _load()
        candidate = body.url.strip()
        parsed = urlsplit(candidate)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise HTTPException(400, "A playlist M3U/M3U8 requer um URL HTTPS válido.")
        if cfg.get("playlist_url"):
            return _internal("test", cfg, {"url": candidate})
        staged = {**cfg, "playlist_url": candidate}
        _write_atomic(staged)
        try:
            return _internal("test", staged, {"url": candidate})
        finally:
            _write_atomic(cfg)


@app.put("/api/iptv")
def iptv_save(body: Update) -> dict:
    with _lock:
        old = _load()
        new = dict(old)
        change_url = body.url is not None and bool(body.url.strip())
        if change_url:
            candidate = body.url.strip()
            p = urlsplit(candidate)
            if p.scheme != "https" or not p.hostname or p.username or p.password:
                raise HTTPException(400, "Indique um URL HTTPS válido para a playlist M3U.")
            if old.get("playlist_url"):
                _internal("test", old, {"url": candidate})
            new["playlist_url"] = candidate
            new["playlist_source"] = "url"
        if body.enabled is not None:
            new["enabled"] = body.enabled
        if new == old:
            return {"ok": True, "changed": False, **_public_config(new)}
        try:
            _write_atomic(new)
            if change_url or (body.enabled is True and old.get("enabled") is False):
                _internal("refresh", new, {})
            _record("saved")
            return {"ok": True, "changed": True, **_public_config(new)}
        except Exception:
            _write_atomic(old)
            try:
                _internal("refresh", old, {})
            except HTTPException:
                pass
            _record("failed", "A actualização não foi aplicada.")
            raise


@app.post("/api/iptv/refresh")
def iptv_refresh() -> dict:
    with _lock:
        cfg = _load()
        try:
            result = _internal("refresh", cfg, {})
        except HTTPException:
            _record("refresh.failed", "A nova playlist não foi carregada.")
            raise
        _record("refreshed")
        return result


@app.get("/api/iptv/channels")
def iptv_channels(q: str = Query(default="", max_length=100),
                  skip: int = Query(default=0, ge=0),
                  limit: int = Query(default=50, ge=1, le=100),
                  order: str = Query(default="number", pattern="^(number|original|name|group)$")) -> dict:
    cfg = _load()
    encoded = httpx.QueryParams({"q": q, "skip": skip, "limit": limit, "order": order})
    return _internal("channels?" + str(encoded), cfg)

@app.post("/api/iptv/channel-preferences")
def iptv_channel_preferences(body: ChannelChange) -> dict:
    """Hide or renumber a channel without modifying the supplier playlist."""
    with _lock:
        _load()
        prefs = _channel_prefs()
        excluded = set(prefs["excluded"])
        ranks = dict(prefs["order"])
        cid = body.channel_id
        if body.action == "hide":
            excluded.add(cid)
        elif body.action == "restore":
            excluded.discard(cid)
        elif body.action == "rank":
            if body.rank is None:
                raise HTTPException(422, "O número do canal é obrigatório.")
            ranks[cid] = body.rank
        elif body.action == "clear_rank":
            ranks.pop(cid, None)
        if len(excluded) > 10000 or len(ranks) > 10000:
            raise HTTPException(422, "Demasiadas preferências de canais.")
        _write_prefs({"excluded": sorted(excluded), "order": ranks})
        _record("channel." + body.action)
        return {"ok": True, "action": body.action, "channelId": cid,
                "excludedCount": len(excluded)}


@app.get("/api/iptv/playback")
def iptv_playback() -> dict:
    """Private Core IPTV jobs, with channel names but no source URLs."""
    cfg = _load()
    if not cfg.get("enabled", False):
        return {"active": [], "count": 0}
    return _internal("playback", cfg)


@app.get("/api/iptv/hidden")
def iptv_hidden() -> dict:
    cfg = _load()
    return _internal("hidden", cfg)

def _is_hls_manifest(payload: str) -> bool:
    """A metadata-only EXT-X-SESSION-DATA tag does not make an M3U catalogue HLS."""
    media = frozenset((
        "#EXT-X-TARGETDURATION", "#EXT-X-MEDIA-SEQUENCE",
        "#EXT-X-PLAYLIST-TYPE", "#EXT-X-MAP",
        "#EXT-X-BYTERANGE", "#EXT-X-PART", "#EXT-X-PRELOAD-HINT",
        "#EXT-X-STREAM-INF", "#EXT-X-I-FRAME-STREAM-INF",
    ))
    tags = {
        line.strip().split(":", 1)[0]
        for line in payload.splitlines() if line.lstrip().startswith("#EXT-X-")
    }
    return bool(tags & media)


def _write_playlist_blob(blob: bytes) -> None:
    IPTV_UPLOAD.parent.mkdir(parents=True, exist_ok=True)
    if IPTV_UPLOAD.is_symlink():
        raise HTTPException(400, "O ficheiro de destino IPTV não pode ser um link simbólico.")
    fd, temporary = tempfile.mkstemp(prefix=".iptv-upload-", dir=IPTV_UPLOAD.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            os.fchmod(file.fileno(), 0o600)
            file.write(blob)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, IPTV_UPLOAD)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@app.post("/api/iptv/upload")
def iptv_upload(body: UploadedPlaylist) -> dict:
    """Replace an operator-owned M3U/M3U8 file, atomically and without Core restart.

    No provider playlist is committed to Git or returned in API responses.
    """
    with _lock:
        name = body.name.lower().strip()
        if not name.endswith((".m3u", ".m3u8")):
            raise HTTPException(422, "O ficheiro deve ser .m3u ou .m3u8.")
        blob = body.text.encode("utf-8")
        if len(blob) > 8 * 1024 * 1024 or not body.text.lstrip("\ufeff \r\n").startswith("#EXTM3U"):
            raise HTTPException(422, "Playlist inválida ou superior a 8 MiB.")
        if _is_hls_manifest(body.text):
            raise HTTPException(422, "Este é um manifesto HLS de reprodução, não uma lista de canais IPTV.")
        cfg = _load()
        previous_blob = None
        if IPTV_UPLOAD.exists():
            if IPTV_UPLOAD.is_symlink() or not IPTV_UPLOAD.is_file():
                raise HTTPException(503, "O ficheiro IPTV existente é inválido.")
            if IPTV_UPLOAD.stat().st_size > 8 * 1024 * 1024:
                raise HTTPException(503, "O ficheiro IPTV existente excede o limite.")
            previous_blob = IPTV_UPLOAD.read_bytes()
        new = dict(cfg)
        new.update(playlist_source="file", playlist_url="")
        try:
            _write_playlist_blob(blob)
            _write_atomic(new)
            result = _internal("refresh", new, {})
            _record("uploaded")
            return {"ok": True, "count": result.get("count", 0),
                    "sourceType": "file", "enabled": new.get("enabled") is not False}
        except Exception:
            _write_atomic(cfg)
            if previous_blob is None:
                IPTV_UPLOAD.unlink(missing_ok=True)
            else:
                _write_playlist_blob(previous_blob)
            if cfg.get("playlist_url") or cfg.get("playlist_source") == "file":
                try:
                    _internal("refresh", cfg, {})
                except HTTPException:
                    pass
            _record("upload.failed", "Playlist anterior preservada.")
            raise
