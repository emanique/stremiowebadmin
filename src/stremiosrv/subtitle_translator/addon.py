"""Independent Stremio subtitle addon (separate from My Library).

Only previously translated subtitles are advertised. Translation is initiated by
an explicit authenticated upload of a selected SRT, not opportunistically on every
playback, and is performed once using installed offline Argos models.
"""
from __future__ import annotations

import hmac
import os
import re
import secrets
import json
import subprocess
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from stremiosrv import cache
from stremiosrv.library import netguard
from stremiosrv.library import labels as labelmod
from stremiosrv.library import addon_model as addonmodel
from stremiosrv.library import state as statemod
from stremiosrv.subtitle_translator import sources as subtitle_sources
from stremiosrv.subtitle_translator import captured as captured_subs
from stremiosrv.api.subs import _decompress
from stremiosrv.subtitle_translator.normalize import to_srt
from stremiosrv.subtitle_translator.hardware import BackendUnavailable, runtime_status
from stremiosrv.subtitle_translator import (
    ModelUnavailable, available_source_languages, delete_title, list_cached, translate_once,
    ensure_translation_intro,
)

router = APIRouter(prefix="/subtitle-translator")
KEY = re.compile(r"^[0-9a-f]{64}$")
HASH = re.compile(r"^[0-9a-f]{40}$")


def _require_qa_mode() -> None:
    """Manual uploader is a private development harness, never a public feature."""
    if os.environ.get("STREMIOSRV_SUBTITLE_TRANSLATOR_QA") != "1":
        raise HTTPException(404)


class Submit(BaseModel):
    infoHash: str
    videoId: str = Field(min_length=1, max_length=256)
    sourceLanguage: str = Field(min_length=2, max_length=8)
    text: str = Field(min_length=1, max_length=4 * 1024 * 1024)


def _root(request: Request) -> str:
    return request.app.state.settings.cache_root


def _token(root: str) -> str:
    folder = Path(root) / ".subtitle-translator"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / ".addon-token"
    try:
        value = path.read_text("utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    except FileNotFoundError:
        pass
    # Token is distinct from the My Library token.
    new = secrets.token_hex(32)
    try:
        with path.open("x", encoding="utf-8") as file:
            file.write(new)
        path.chmod(0o600)
    except FileExistsError:
        return path.read_text("utf-8").strip()
    return new


def _guard(request: Request, token: str) -> None:
    settings = request.app.state.settings
    peer = request.client.host if request.client else ""
    ip = netguard.client_ip(peer, request.headers.get("x-forwarded-for", ""))
    if not netguard.is_allowed(ip, netguard.parse_allow(settings.library_addon_allow)):
        raise HTTPException(404)
    if not hmac.compare_digest(token, _token(_root(request))):
        raise HTTPException(404)


def _title_present(root: str, infohash: str) -> bool:
    if not HASH.fullmatch(infohash):
        return False
    for name, digest in cache.load_name_index(root).items():
        if str(digest).lower() != infohash:
            continue
        # Must be a direct child and not a protected internal cache file.
        if name != Path(name).name or name in cache.PROTECTED:
            continue
        path = Path(root) / name
        if path.exists() and not path.is_symlink():
            return True
    return False


def _origin(request: Request) -> str:
    """Use the explicitly configured trusted TLS endpoint for SRT delivery.

    The API is served internally over HTTP, but external Stremio clients must
    never receive a Docker/loopback HTTP subtitle URL.
    """
    configured = os.environ.get("STREMIOSRV_SUBTITLE_PUBLIC_ORIGIN", "").strip().rstrip("/")
    if configured:
        parsed = urlsplit(configured)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.query
                or parsed.fragment or parsed.path not in ("", "/")):
            raise HTTPException(503, detail="Invalid subtitle public origin")
        return configured
    peer = request.client.host if request.client else ""
    if netguard._is_loopback(peer):
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    else:
        proto = request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


@router.get("/{token}/manifest.json")
def manifest(token: str, request: Request) -> dict:
    _guard(request, token)
    return {
        "id": "org.stremiosrv.subtitle-translator",
        "version": "0.1.0",
        "name": "Stremio Server Addon · Tradutor",
        "description": "Persistent offline pt-PT subtitles for locally cached titles",
        "resources": ["subtitles"],
        "types": ["movie", "series", "other"],
        "catalogs": [],
        "idPrefixes": ["tt", "stremiosrv:"],
    }



def _linked_hashes(root: str, video_id: str, request: Request | None = None,
                   type_: str = "movie", extra: str = "") -> list[str]:
    """Bind source discovery to actual cached releases, never a guessed filename."""
    # My Library uses stable stremiosrv:<infohash>[:<fileIdx>] IDs.
    # Those IDs already prove the exact cached torrent. They need no IMDb
    # label and no guessed filename or metadata association.
    own = addonmodel.parse_id(video_id)
    if own is not None:
        ih, file_idx = own
        if _title_present(root, ih):
            if file_idx is None:
                return [ih]
            if request is not None:
                handle = request.app.state.engine.get(ih)
                if handle is not None and handle.has_metadata():
                    if 0 <= file_idx < handle.num_files():
                        return [ih]
        return []
    base = video_id.split(":", 1)[0]
    matches: list[str] = []
    for ih, record in labelmod.load(root).items():
        if not HASH.fullmatch(str(ih)) or not isinstance(record, dict):
            continue
        if record.get("metaId") == base and _title_present(root, ih):
            stored = record.get("videoId")
            if len(video_id.split(":")) == 3 and stored and stored != video_id:
                continue
            matches.append(ih)
    if not matches and request is not None and extra:
        # Independent of installing My Library: match the actual playing file
        # against the primary torrent cache by reported byte size and filename.
        parsed = addonmodel.parse_extra(extra)
        if parsed.get("videoSize"):
            state = statemod.build(root, request.app.state.engine)
            inferred = addonmodel.learn_labels(state, type_, video_id, parsed)
            candidates = list(dict.fromkeys(ih for ih, _ in inferred))
            if len(candidates) == 1 and _title_present(root, candidates[0]):
                # The playback client supplied an exact byte-size/filename
                # match to one local media file. Persist this factual link so
                # future subtitle requests can resolve the title without
                # re-probing the entire cache; never override an owner label.
                ih = candidates[0]
                for inferred_hash, inferred_label in inferred:
                    if inferred_hash == ih:
                        labelmod.learn(root, ih, inferred_label)
                        break
                matches = candidates
    return matches[:4]


_SIDECAR_SUFFIX = re.compile(
    r"\\.(?P<lang>en|eng|fr|fre|fra|es|spa)"
    r"(?:\\.(?:forced|sdh))?\\.(?:srt|vtt|ass|ssa)$", re.IGNORECASE
)


def _existing_local_tracks(root: str, ih: str, video_id: str) -> list[dict]:
    """Safe external subtitle files already beside a principal cached video.

    No arbitrary directory scan and no fallback to another movie. Matches a
    complete cached media basename + explicit language suffix.
    """
    result: list[dict] = []
    media = _cached_media_for_infohash(root, ih, video_id)
    if media is None:
        return result
    for lang in ("en", "fr", "es"):
        for ext in ("srt", "vtt", "ass", "ssa"):
            name = f"{media.stem}.{lang}.{ext}"
            sidecar = media.parent / name
            if (not sidecar.is_file() or sidecar.is_symlink()
                    or not 0 < sidecar.stat().st_size <= subtitle_sources.MAX_TEXT):
                continue
            result.append({
                "id": subtitle_sources.source_id("local:" + str(sidecar.relative_to(root)), lang, video_id),
                "sourceLanguage": lang, "name": name[:80],
                "path": sidecar, "kind": "local",
            })
    return result


_EMBEDDED_LANGUAGE = {
    "en": "en", "eng": "en", "english": "en",
    "fr": "fr", "fra": "fr", "fre": "fr", "french": "fr",
    "es": "es", "spa": "es", "spanish": "es",
}
_TEXT_SUBTITLE_CODECS = frozenset(("subrip", "ass", "ssa", "webvtt", "mov_text", "text"))


_VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v", ".ts"})


def _cached_media_for_infohash(root: str, ih: str, video_id: str = "") -> Path | None:
    """Resolve only media in this torrent's download, including one-level folders.

    Do not search other titles, follow symlinks or guess which episode belongs
    to a multi-file torrent. For packs, only an explicit Stremio file index may
    choose a file; until then return no candidate.
    """
    for name, digest in cache.load_name_index(root).items():
        if str(digest).lower() != ih or name != Path(name).name or name in cache.PROTECTED:
            continue
        target = Path(root) / name
        if target.is_symlink():
            continue
        if target.is_file() and target.suffix.lower() in _VIDEO_EXTENSIONS:
            return target
        if not target.is_dir():
            continue
        candidates = sorted(
            (p for p in target.iterdir()
             if p.is_file() and not p.is_symlink() and p.suffix.lower() in _VIDEO_EXTENSIONS),
            key=lambda p: p.name.casefold(),
        )
        if len(candidates) == 1:
            return candidates[0]
        # Without the exact torrent file-index-to-path mapping, a multi-video
        # directory is ambiguous. Returning None is safer than a wrong SRT.
        return None
    return None


def _embedded_media_complete(root: str, ih: str, media: Path, request: Request | None = None) -> bool:
    """Protect persistent subtitles from incomplete libtorrent media.

    A sparse preallocated file's apparent length is not its completion. Prefer
    libtorrent piece verification when a handle exists; otherwise cross-check
    the recorded media size and actual extents. Fail closed on inconsistencies.
    This check does not affect downloads, playback or other subtitle addons.
    """
    if request is not None:
        engine = getattr(request.app.state, "engine", None)
        handle = engine.get(ih) if engine is not None else None
        if handle is not None:
            try:
                for idx, path in enumerate(handle.file_paths()):
                    if Path(path).name == media.name:
                        return bool(handle.file_complete(idx))
                return False
            except (AttributeError, RuntimeError, OSError, ValueError):
                return False
    try:
        stat = media.stat()
        if stat.st_size <= 0:
            return False
        label = labelmod.load(root).get(ih, {})
        recorded = (label.get("file") or {}) if isinstance(label, dict) else {}
        if recorded.get("name") == media.name:
            expected = recorded.get("size")
            if type(expected) is int and expected > 0 and stat.st_size != expected:
                return False
        # A hole in a preallocated torrent is strong evidence of absent pieces.
        return cache.data_bytes(str(media), stat) >= stat.st_size * 0.999
    except (OSError, ValueError):
        return False


def _embedded_tracks(root: str, ih: str, video_id: str, request: Request | None = None) -> list[dict]:
    """Discover supported text subtitle streams embedded in the cached video.

    FFprobe reads container metadata only. Do not transcode or modify the
    source during subtitle listing. Unsupported image-based subtitles such as
    PGS/DVD are excluded because they require OCR, not text translation.
    """
    media = _cached_media_for_infohash(root, ih, video_id)
    if media is None or not _embedded_media_complete(root, ih, media, request):
        return []
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=index,codec_type,codec_name:stream_tags=language,title",
             "-of", "json", str(media)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=8, check=False,
        )
        if proc.returncode or len(proc.stdout) > 256 * 1024:
            return []
        streams = json.loads(proc.stdout).get("streams", [])
    except (ValueError, OSError, subprocess.SubprocessError):
        return []
    options = []
    for item in streams:
        if item.get("codec_type") != "subtitle":
            continue
        lang = _EMBEDDED_LANGUAGE.get(
            str((item.get("tags") or {}).get("language") or "").lower()
        )
        codec = str(item.get("codec_name") or "").lower()
        index = item.get("index")
        if (not lang or codec not in _TEXT_SUBTITLE_CODECS
                or type(index) is not int or index < 0):
            continue
        title = str((item.get("tags") or {}).get("title") or "").strip()[:40]
        label = f"Faixa embutida #{index} ({codec})"
        if title:
            label += f" · {title}"
        options.append({
            "id": subtitle_sources.source_id(
                f"embedded:{ih}:{index}", lang, video_id),
            "sourceLanguage": lang, "name": label,
            "kind": "embedded", "index": index,
        })
    return options


def _extract_embedded_srt(root: str, ih: str, index: int, request: Request | None = None) -> str:
    """Extract a selected embedded *text* subtitle stream into SRT.

    Stream index is revalidated from FFprobe results on each selection.
    FFmpeg runs locally with no network protocols, a bounded timeout and
    output-size guard. The input video is never changed.
    """
    tracks = _embedded_tracks(root, ih, f"stremiosrv:{ih}", request)
    if not any(track["index"] == index for track in tracks):
        raise ValueError("selected embedded subtitle stream is unavailable")
    media = _cached_media_for_infohash(root, ih)
    if media is None:
        raise ValueError("cached video no longer exists")
    try:
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-protocol_whitelist", "file,crypto,data",
             "-i", str(media), "-map", f"0:{index}", "-vn", "-an",
             "-c:s", "srt", "-f", "srt", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False, timeout=40,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("embedded subtitle extraction unavailable") from exc
    if proc.returncode or not 0 < len(proc.stdout) <= subtitle_sources.MAX_TEXT:
        raise ValueError("embedded subtitle extraction failed or exceeded size limit")
    return to_srt(proc.stdout)



def _provider_video_id(root: str, video_id: str, infohash: str) -> str:
    """External Stremio subtitle addons require IMDb IDs, not My Library IDs.

    Only the cache owner's existing infohash -> verified label can supply this
    mapping. It does not alter the stored content ID or torrent ownership.
    """
    if not video_id.startswith("stremiosrv:"):
        return video_id
    entry = labelmod.load(root).get(infohash, {})
    meta = entry.get("metaId") if isinstance(entry, dict) else None
    if isinstance(meta, str) and re.fullmatch(r"tt[0-9]{2,12}", meta):
        return meta
    return video_id



_TRANSLATOR_SETTINGS = Path("/config/subtitle-translator-settings.json")
_DEFAULT_SOURCE_PRIORITY = ("en", "fr", "es")


def _installed_source_codes() -> tuple[str, ...]:
    """Model registry is scanned per request; no restart after installing a pair."""
    from stremiosrv.subtitle_translator.local_engine import supported_source_languages
    sources = supported_source_languages()
    return tuple(code for code in _DEFAULT_SOURCE_PRIORITY if code in sources) + tuple(
        sorted(sources.difference(_DEFAULT_SOURCE_PRIORITY)))


def _translation_preferences() -> tuple[str, ...]:
    """Per-request persisted source-language order; never affects other addons."""
    installed = _installed_source_codes()
    try:
        value = json.loads(_TRANSLATOR_SETTINGS.read_text("utf-8"))
        entries = value.get("languages")
        if (not isinstance(entries, list) or len(entries) != len(set(map(str, entries)))
                or not all(isinstance(x, str) and x in installed for x in entries)):
            return installed
        return tuple(entries)
    except (OSError, ValueError, TypeError):
        return installed


def _ranked_options(options: list[dict]) -> list[dict]:
    enabled = _translation_preferences()
    priority = {lang: idx for idx, lang in enumerate(enabled)}
    return sorted(
        (item for item in options if item["sourceLanguage"].split("-")[0] in priority),
        key=lambda item: (
        priority[item["sourceLanguage"].split("-")[0]],
        0 if item.get("kind") == "embedded" else
        1 if item.get("kind") == "local" else 2,
        item["name"].casefold(), item["id"],
    ))


def _selected_file(root: str, ih: str, video_id: str, source_id: str) -> Path | None:
    for meta in list_cached(root, ih, video_id):
        if meta.get("selectionId") == source_id:
            candidate = Path(root) / ".subtitle-translator" / ih / (meta["key"] + ".srt")
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
    return None


@router.get("/{token}/subtitles/{type_}/{video_id}.json")
@router.get("/{token}/subtitles/{type_}/{video_id}/{extra}.json")
def subtitles(token: str, type_: str, video_id: str, request: Request,
              extra: str = "") -> dict:
    _guard(request, token)
    root = _root(request)
    hashes = _linked_hashes(root, video_id, request, type_, extra)
    if not hashes:
        return {"subtitles": []}
    prefix = _origin(request) + f"/subtitle-translator/{token}/"
    found: list[dict] = []
    for ih in hashes:
        for meta in list_cached(root, ih, video_id):
            found.append({
                "id": meta["key"], "lang": "pt-PT",
                "url": prefix + "file/" + ih + "/" + meta["key"] + ".srt",
                "name": "Em cache · " + (meta.get("sourceAddon") or "Stremio Server Addon")
                        + " · " + meta.get("source", "").upper()
                        + " → PT · " + (meta.get("sourceName") or meta["key"][:8]),
            })
    config = subtitle_sources.configured_sources(request.app.state.settings)
    options: list[dict] = []
    for ih in hashes:
        if config:
            try:
                options.extend(subtitle_sources.discover(
                    config, type_, _provider_video_id(root, video_id, ih), extra))
            except ValueError:
                pass
        options.extend(_existing_local_tracks(root, ih, video_id))
        options.extend(captured_subs.sources_for_title(root, ih))
        options.extend(_embedded_tracks(root, ih, video_id, request))
    available = available_source_languages()
    # Explicit user priority: English, French, Spanish, then any other
    # supported languages. Keep original addon track identities stable.
    options = _ranked_options(options)
    for ih in hashes:
        for item in options:
            if item["sourceLanguage"].split("-")[0] not in available:
                continue
            if _selected_file(root, ih, video_id, item["id"]):
                continue
            url = (
                prefix + "select/" + ih + "/" + quote(video_id, safe=":") + "/"
                + item["id"] + ".srt?type_=" + quote(type_)
                + "&extra=" + quote(extra, safe="")
            )
            found.append({
                "id": item["id"], "lang": "pt-PT",
                "url": url,
                # Each pt-PT variant is an explicit SOURCE choice in the native
                # Stremio subtitle menu. No automatic source selection occurs.
                "name": (item.get("sourceAddon") or (
                            "Legenda embutida" if item.get("kind") == "embedded"
                            else "Legenda local" if item.get("kind") == "local"
                            else "Addon de legendas"))
                        + " · " + item["sourceLanguage"].upper()
                        + " → PT · " + item["name"],
            })
    return {"subtitles": found[:100]}



@router.get("/{token}/select/{infohash}/{video_id}/{selection_id}.srt")
def translate_selected(
    token: str, infohash: str, video_id: str, selection_id: str,
    request: Request, type_: str = Query("movie"), extra: str = Query(""),
):
    """Translate the track selected in Stremio, and serve cached subtitles."""
    _guard(request, token)
    root = _root(request)
    if not HASH.fullmatch(infohash) or not KEY.fullmatch(selection_id):
        raise HTTPException(404)
    if infohash not in _linked_hashes(root, video_id, request, type_, extra):
        raise HTTPException(404)
    existing = _selected_file(root, infohash, video_id, selection_id)
    if existing:
        ensure_translation_intro(existing)
        return FileResponse(existing, media_type="application/x-subrip")
    config = subtitle_sources.configured_sources(request.app.state.settings)
    try:
        options = subtitle_sources.discover(
            config, type_, _provider_video_id(root, video_id, infohash), extra) if config else []
    except ValueError:
        options = []
    options.extend(_existing_local_tracks(root, infohash, video_id))
    options.extend(captured_subs.sources_for_title(root, infohash))
    options.extend(_embedded_tracks(root, infohash, video_id, request))
    options = _ranked_options(options)
    selected = next((x for x in options if x["id"] == selection_id), None)
    if selected is None:
        raise HTTPException(404, detail="source subtitle unavailable")
    try:
        if selected.get("kind") == "embedded":
            original = _extract_embedded_srt(root, infohash, selected["index"], request)
        else:
            if selected.get("kind") in ("local", "captured"):
                path = selected["path"]  # generated by strict cache source lookup
                if path.is_symlink() or not path.is_file():
                    raise HTTPException(404, detail="source subtitle removed")
                raw = path.read_bytes()
            else:
                raw = subtitle_sources.fetch_bytes(selected["url"], subtitle_sources.MAX_TEXT)
            original = to_srt(_decompress(raw, ""))
        result = translate_once(root, infohash, video_id, original,
                                selected["sourceLanguage"], selection_id=selection_id,
                                source_name=selected.get("name", ""),
                                source_addon=selected.get("sourceAddon") or (
                                    "Legenda capturada" if selected.get("kind") == "captured"
                                    else "Legenda embutida" if selected.get("kind") == "embedded"
                                    else "Legenda local" if selected.get("kind") == "local"
                                    else "Addon de legendas"))
        if not _title_present(root, infohash):
            delete_title(root, infohash)
            raise HTTPException(409, detail="title removed during translation")
    except (ModelUnavailable, BackendUnavailable) as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except (ValueError, OSError, TimeoutError) as exc:
        raise HTTPException(502, detail="source subtitle could not be translated") from exc
    return FileResponse(result["file"], media_type="application/x-subrip")


@router.get("/{token}/file/{infohash}/{key}.srt")
def file_by_title(token: str, infohash: str, key: str, request: Request):
    _guard(request, token)
    if not HASH.fullmatch(infohash) or not KEY.fullmatch(key):
        raise HTTPException(404)
    root = _root(request)
    if not _title_present(root, infohash):
        raise HTTPException(404)
    path = Path(root) / ".subtitle-translator" / infohash / (key + ".srt")
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404)
    ensure_translation_intro(path)
    return FileResponse(path, media_type="application/x-subrip")


@router.post("/{token}/translate")
def submit(token: str, body: Submit, request: Request) -> dict:
    _require_qa_mode()
    _guard(request, token)
    root = _root(request)
    ih = body.infoHash.lower()
    if not _title_present(root, ih):
        raise HTTPException(404, detail="title is not in the principal cache")
    try:
        result = translate_once(
            root, ih, body.videoId, body.text, body.sourceLanguage,
            target="pt-PT",
        )
        if not _title_present(root, ih):
            delete_title(root, ih)
            raise HTTPException(409, detail="source title removed during translation")
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except ModelUnavailable as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    return {"cached": result["cached"], "key": result["key"], "language": "pt-PT"}


@router.get("/{token}/titles.json")
def cached_titles(token: str, request: Request) -> dict:
    """Read-only title choices for the addon's own management page."""
    _require_qa_mode()
    _guard(request, token)
    root = _root(request)
    titles = []
    for name, ih in cache.load_name_index(root).items():
        ih = str(ih).lower()
        if HASH.fullmatch(ih) and _title_present(root, ih):
            titles.append({"name": name, "infoHash": ih})
    return {"titles": sorted(titles, key=lambda item: item["name"].casefold())[:500]}


@router.get("/{token}/models.json")
def models(token: str, request: Request) -> dict:
    """Report locally installed Argos models, never install on a GET."""
    _guard(request, token)
    config = subtitle_sources.configured_sources(request.app.state.settings)
    try:
        source_count = len(subtitle_sources.provider_roots(config))
    except ValueError:
        source_count = 0
    supported = available_source_languages()
    return {
        "installed": bool(supported),
        "target": "pt-PT",
        "sources": sorted(supported),
        "sourceAddonsConfigured": bool(source_count),
        "acceleration": runtime_status(),
        "notice": "Generic pt models do not guarantee European Portuguese spelling",
    }


@router.get("/{token}/manage")
def manage(token: str, request: Request) -> HTMLResponse:
    """Standalone local QA page: source subtitle chosen by the operator."""
    _require_qa_mode()
    _guard(request, token)
    from stremiosrv.subtitle_translator.management import PAGE
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})

def installed_manifest_url(root: str, origin: str) -> str:
    return origin.rstrip("/") + "/subtitle-translator/" + _token(root) + "/manifest.json"
