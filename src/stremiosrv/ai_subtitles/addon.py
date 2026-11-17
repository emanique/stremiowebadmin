"""Independent Stremio addon exposing generated pt-PT subtitles only.

The generation worker is separate: listing a subtitle never transcribes audio.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse

from stremiosrv.ai_subtitles.generator import cache_key
from stremiosrv.subtitle_translator import addon as translator

router = APIRouter(prefix="/ai-subtitles")
HASH = re.compile(r"^[a-f0-9]{40}$")
KEY = re.compile(r"^[a-f0-9]{64}$")


@router.get("/{token}/manifest.json")
def manifest(token: str, request: Request) -> dict:
    translator._guard(request, token)
    return {
        "id": "org.stremiosrv.ai-subtitles",
        "version": "0.1.0",
        "name": "AI Subtitle Generator",
        "description": "Offline audio transcription and translation into Portuguese (Portugal)",
        "resources": ["subtitles"],
        "types": ["movie", "series", "other"],
        "catalogs": [],
        "idPrefixes": ["tt", "stremiosrv:"],
    }


@router.get("/{token}/status.json")
def runtime_status(token: str, request: Request) -> dict:
    translator._guard(request, token)
    automatic = os.environ.get("STREMIOSRV_AI_SUBTITLES_ENABLED", "").lower() in ("1", "true", "yes")
    on_demand = os.environ.get("STREMIOSRV_AI_ON_DEMAND_ENABLED", "").lower() in ("1", "true", "yes")
    enabled = automatic or on_demand
    model = Path(os.environ.get("STREMIOSRV_AI_WHISPER_MODEL", ""))
    binary = os.environ.get("STREMIOSRV_AI_WHISPER_BIN", "whisper-cli")
    model_ok = model.is_file() and not model.is_symlink() and model.stat().st_size > 1024 * 1024
    resolved_bin = binary if Path(binary).is_file() else shutil.which(binary)
    binary_ok = bool(resolved_bin and os.access(resolved_bin, os.X_OK))
    ready = enabled and model_ok and binary_ok
    return {
        "enabled": enabled,
        "onDemandEnabled": on_demand,
        "automaticScanEnabled": automatic,
        "modelInstalled": model_ok,
        "binaryInstalled": binary_ok,
        "ready": ready,
        "status": ("Ready" if ready else "Offline Whisper model or executable missing"
                   if enabled else "AI Subtitle Generator disabled by configuration"),
    }


@router.get("/{token}/jobs.json")
def jobs(token: str, request: Request) -> dict:
    translator._guard(request, token)
    worker = getattr(request.app.state, "ai_subtitle_worker", None)
    if worker is None:
        return {"enabled": False, "active": None, "jobs": [], "status": "worker unavailable"}
    demand = getattr(request.app.state, "ai_on_demand", None)
    if demand is not None:
        snap = demand.snapshot()
        active = snap["active"]
        return {
            "enabled": True, "automaticScan": False, "mode": "on_demand",
            "active": active[0] if active else None,
            "jobs": active, "recentClientEvents": snap.get("recentClientEvents", []),
            "heartbeatTimeoutSeconds": snap["heartbeatTimeoutSeconds"],
            "coverage": "Only explicitly selected sessions with a reported playhead",
        }
    return worker.snapshot()


class StartDemand(BaseModel):
    infohash: str = Field(min_length=40, max_length=40)
    fileIndex: int = Field(ge=0, le=999)
    audioIndex: int = Field(default=0, ge=0, le=20)
    positionSeconds: float = Field(ge=0, le=86400)


class Heartbeat(BaseModel):
    positionSeconds: float = Field(ge=0, le=86400)


def _demand(request: Request):
    instance = getattr(request.app.state, "ai_on_demand", None)
    if instance is None:
        raise HTTPException(503, detail="On-demand AI subtitles not enabled")
    return instance


class ClientPlaybackEvent(BaseModel):
    event: str = Field(min_length=3, max_length=32)
    httpStatus: int = Field(default=0, ge=0, le=599)


@router.post("/{token}/demand/client-event")
def demand_client_event(token: str, request: Request, data: ClientPlaybackEvent) -> dict:
    translator._guard(request, token)
    try:
        _demand(request).record_client_event(data.event, data.httpStatus)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return {"received": True}


@router.post("/{token}/demand/start")
def demand_start(token: str, request: Request, data: StartDemand) -> dict:
    translator._guard(request, token)
    try:
        return _demand(request).start(data.infohash, data.fileIndex, data.positionSeconds,
                                      data.audioIndex)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except Exception as exc:
        from stremiosrv.ai_subtitles.generator import GenerationUnavailable
        if isinstance(exc, GenerationUnavailable):
            raise HTTPException(409, detail=str(exc)) from exc
        raise


@router.post("/{token}/demand/{session_id}/heartbeat")
def demand_heartbeat(token: str, session_id: str, request: Request, data: Heartbeat) -> dict:
    translator._guard(request, token)
    try:
        return _demand(request).heartbeat(session_id, data.positionSeconds)
    except (KeyError, ValueError) as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.post("/{token}/demand/{session_id}/stop")
def demand_stop(token: str, session_id: str, request: Request) -> dict:
    translator._guard(request, token)
    _demand(request).stop(session_id)
    return {"stopped": True}


@router.get("/{token}/demand/{session_id}/status.json")
def demand_status(token: str, session_id: str, request: Request) -> dict:
    translator._guard(request, token)
    try:
        return _demand(request).status(session_id)
    except KeyError as exc:
        raise HTTPException(404, detail="AI subtitle session expired") from exc


@router.get("/{token}/demand/{session_id}/native.srt")
def demand_native_srt(token: str, session_id: str, request: Request):
    """Distinct SRT snapshot per explicitly started native player session.

    Passive GETs never start Whisper. Clients must explicitly refresh this
    session-scoped URL; Stremio Desktop does not promise automatic reload.
    """
    translator._guard(request, token)
    try:
        data = _demand(request).session_srt(session_id)
    except KeyError as exc:
        raise HTTPException(404, detail="AI subtitle session expired") from exc
    return PlainTextResponse(
        data, media_type="application/x-subrip",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@router.get("/{token}/demand/{session_id}/subtitles.srt")
def demand_subtitles(token: str, session_id: str, request: Request):
    """Return a fresh SRT snapshot; the client must explicitly refresh it.

    Native Stremio clients may cache the original fetch, so no automatic
    live-update guarantee is implied by this endpoint.
    """
    translator._guard(request, token)
    try:
        session = _demand(request).status(session_id)
    except KeyError as exc:
        raise HTTPException(404, detail="AI subtitle session expired") from exc
    infohash, index = session["infohash"], session["fileIndex"]
    audio_index = session.get("audioIndex", 0)
    folder = (Path(translator._root(request)) / ".ai-subtitles" / infohash
              / "on-demand" / str(index) / str(audio_index))
    # Every request reads the SAME cumulative file, rather than rebuilding
    # an ephemeral SRT window that forgets previous subtitles.
    cumulative = folder / "progressive.srt"
    if cumulative.is_file() and not cumulative.is_symlink():
        return PlainTextResponse(
            cumulative.read_text("utf8"), media_type="application/x-subrip",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                     "X-AI-Subtitle-Stage": session["stage"]})
    from stremiosrv.ai_subtitles.generator import _stamp
    start = max(0, int(session["positionSeconds"] * 1000))
    notice = ("A preparar legendas AI em português…"
              if session["stage"] != "buffering_audio"
              else "A aguardar áudio disponível no torrent…")
    return PlainTextResponse(
        f"1\n{_stamp(start)} --> {_stamp(start + 8000)}\n{notice}\n",
        media_type="application/x-subrip",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                 "X-AI-Subtitle-Stage": session["stage"]})


@router.get("/{token}/subtitles/{type_}/{video_id}.json")
@router.get("/{token}/subtitles/{type_}/{video_id}/{extra}.json")
def subtitles(token: str, type_: str, video_id: str, request: Request, extra: str = "") -> dict:
    translator._guard(request, token)
    if type_ not in ("movie", "series", "other"):
        return {"subtitles": []}
    root = translator._root(request)
    model_name = Path(os.environ.get("STREMIOSRV_AI_WHISPER_MODEL", "")).name
    if not model_name or model_name == ".":
        return {"subtitles": []}
    entries: list[dict] = []
    origin = translator._origin(request)
    own = translator.addonmodel.parse_id(video_id)
    if own is not None and own[1] is not None and translator._title_present(root, own[0]):
        # A fully cached episode can outlive its libtorrent handle. Its explicit
        # stremiosrv:<infohash>:<file-index> identifier is unambiguous; the
        # existing translation helper otherwise requires a live handle.
        hashes = [own[0]]
    else:
        hashes = translator._linked_hashes(root, video_id, request, type_, extra)
    for ih in hashes:
        handle = request.app.state.engine.get(ih) if request.app.state.engine else None
        if not translator._title_present(root, ih):
            continue
        if own is not None and own[0] == ih and own[1] is not None:
            indices = [own[1]]
        elif handle is not None and handle.has_metadata() and handle.num_files() == 1:
            indices = [0]
        else:
            # IMDb-linked metadata can point to a completed, unloaded torrent.
            # Only use the exact recorded file index when a matching, complete
            # single-file torrent has been verified from libtorrent fastresume.
            from stremiosrv.ai_subtitles.resume_scan import complete_cached_files
            matches = [(idx, count) for digest, idx, _media, count in
                       complete_cached_files(Path(root), set()) if digest == ih]
            if len(matches) != 1 or matches[0][1] != 1:
                continue
            indices = [matches[0][0]]
        for index in indices:
            if index < 0 or (handle is not None and handle.has_metadata() and index >= handle.num_files()):
                continue
            key = cache_key(ih, index, 0, model_name)
            file_path = Path(root) / ".ai-subtitles" / ih / (key + ".srt")
            demand = os.getenv("STREMIOSRV_AI_ON_DEMAND_ENABLED", "false").lower() in (
                "true", "1", "yes")
            if demand:
                entries.append({
                    "id": "ai-demand-" + key,
                    "lang": "pt-PT",
                    "name": "AI Subtitle Generator · Português (pt-PT) · A pedido",
                    "url": f"{origin}/ai-subtitles/{token}/placeholder/{ih}/{index}/{key}.srt",
                })
                continue
            if not file_path.is_file() or file_path.is_symlink():
                continue
            entries.append({
                "id": key,
                "lang": "pt-PT",
                "name": "AI Subtitle Generator · Português (pt-PT)",
                "url": f"{origin}/ai-subtitles/{token}/file/{ih}/{key}.srt",
            })
    return {"subtitles": entries}


@router.get("/{token}/placeholder/{infohash}/{file_index}/{key}.srt")
def demand_placeholder(token: str, infohash: str, file_index: int, key: str, request: Request):
    """Selectable subtitle track, never a trigger by itself.

    The loader observes an actual user selection and posts playback position.
    Fetching this SRT cannot start processing (prefetch-safe).
    """
    translator._guard(request, token)
    if not HASH.fullmatch(infohash) or not KEY.fullmatch(key) or not 0 <= file_index < 1000:
        raise HTTPException(404)
    if os.getenv("STREMIOSRV_AI_ON_DEMAND_ENABLED", "false").lower() not in ("true", "1", "yes"):
        raise HTTPException(404)
    model = Path(os.environ.get("STREMIOSRV_AI_WHISPER_MODEL", "")).name
    if key != cache_key(infohash, file_index, 0, model):
        raise HTTPException(404)
    if not translator._title_present(translator._root(request), infohash):
        raise HTTPException(404)
    cumulative = (Path(translator._root(request)) / ".ai-subtitles" / infohash
                  / "on-demand" / str(file_index) / "0" / "progressive.srt")
    if cumulative.is_file() and not cumulative.is_symlink():
        return PlainTextResponse(
            cumulative.read_text("utf8"), media_type="application/x-subrip",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    return PlainTextResponse(
        "1\n00:00:00,000 --> 00:00:08,000\nA preparar legendas AI…\n",
        media_type="application/x-subrip",
        headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/{token}/file/{infohash}/{key}.srt")
def file_by_hash(token: str, infohash: str, key: str, request: Request):
    translator._guard(request, token)
    if not HASH.fullmatch(infohash) or not KEY.fullmatch(key):
        raise HTTPException(404)
    root = translator._root(request)
    if not translator._title_present(root, infohash):
        raise HTTPException(404)
    model_name = Path(os.environ.get("STREMIOSRV_AI_WHISPER_MODEL", "")).name
    target = Path(root) / ".ai-subtitles" / infohash / (key + ".srt")
    if not target.is_file() or target.is_symlink():
        raise HTTPException(404)
    return FileResponse(target, media_type="application/x-subrip")
