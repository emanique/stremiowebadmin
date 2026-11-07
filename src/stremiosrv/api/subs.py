"""Subtitle + opensub-hash API: matching key for subtitle addons, embedded-track listing/extraction."""
from __future__ import annotations

import gzip
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import zlib
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from stremiosrv import metrics
from stremiosrv.api.media_fetch import resolve_media_input
from stremiosrv.proxy import client, dest, upstream
from stremiosrv.stream.fileserver import file_disk_path
from stremiosrv.subs.opensub import opensubtitles_hash_and_size
from stremiosrv.transcode.probe import ProbeTimeoutError, probe_media

try:  # proper charset detection (Cyrillic/legacy subs); degrade gracefully if absent
    from charset_normalizer import from_bytes as _detect_bytes
except ImportError:  # pragma: no cover
    _detect_bytes = None

logger = logging.getLogger(__name__)
router = APIRouter()

# Completed HLS WebVTT windows are small. Keep a bounded in-process LRU and one lock per
# window so browser retries/concurrent playlist requests never launch duplicate ffmpeg jobs.
_VTT_WINDOW_CACHE_MAX = 256
_VTT_WINDOW_TIMEOUT = 25.0
_vtt_window_guard = threading.Lock()
_vtt_window_cache: OrderedDict[tuple, bytes] = OrderedDict()
_vtt_window_locks: dict[tuple, threading.Lock] = {}


def _vtt_window_lock(key: tuple) -> threading.Lock:
    with _vtt_window_guard:
        return _vtt_window_locks.setdefault(key, threading.Lock())


def _vtt_cache_get(key: tuple) -> bytes | None:
    with _vtt_window_guard:
        payload = _vtt_window_cache.get(key)
        if payload is not None:
            _vtt_window_cache.move_to_end(key)
        return payload


def _vtt_cache_put(key: tuple, payload: bytes) -> None:
    if not payload:
        return
    with _vtt_window_guard:
        _vtt_window_cache[key] = payload
        _vtt_window_cache.move_to_end(key)
        while len(_vtt_window_cache) > _VTT_WINDOW_CACHE_MAX:
            old_key, _ = _vtt_window_cache.popitem(last=False)
            # Keep the lock table bounded with the payload LRU, but never replace a lock that a
            # concurrent request is currently holding (that would defeat in-flight deduplication).
            old_lock = _vtt_window_locks.get(old_key)
            if old_lock is not None and not old_lock.locked():
                _vtt_window_locks.pop(old_key, None)


def _reset_vtt_window_cache() -> None:
    """Test helper; production cache is naturally reset with the server process."""
    with _vtt_window_guard:
        _vtt_window_cache.clear()
        _vtt_window_locks.clear()


def _decompress(raw: bytes, content_encoding: str) -> bytes:
    """Undo gzip/deflate if the CDN compressed the body (urllib doesn't auto-decompress). Sniffs the
    gzip magic bytes too, since some hosts gzip without the header."""
    enc = (content_encoding or "").lower()
    if "gzip" in enc or raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw)
        except OSError:
            return raw
    if "deflate" in enc:
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                return zlib.decompress(raw, wbits)
            except zlib.error:
                continue
    return raw


def decode_subtitle(raw: bytes) -> str:
    """Decode a subtitle body to text, detecting the charset. A Windows-1251 (Cyrillic) or other
    legacy-encoded sub must NOT be forced through UTF-8 — that yields replacement junk that strict
    players (ExoPlayer) drop, while mpv would have coped. Returns clean Unicode."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8")  # the common, clean case
    except UnicodeDecodeError:
        pass
    if _detect_bytes is not None:
        best = _detect_bytes(raw).best()
        if best is not None:
            return str(best)
    return raw.decode("windows-1251", errors="replace")  # Cyrillic-biased last resort

# Stremio passes videoUrl as our own stream URL:
# .../<40-hex-infohash>/<fileIdx>[?...]
#
# fileIdx may be -1. stremio-core uses that value to mean "choose the
# playable file for me"; playback.serve() resolves it through GuessFileIdx.
# Keep that signed index intact here so HLS/subtitle routes preserve the
# same public stream-URL contract.
_STREAM_RE = re.compile(r"/([0-9a-fA-F]{40})/(-1|\d+)(?=[/?#]|$)")


def parse_stream_url(url: str) -> tuple[str, int] | None:
    m = _STREAM_RE.search(url)
    return (m.group(1).lower(), int(m.group(2))) if m else None

def stream_file_idx(h, idx: int) -> int:
    """Resolve Core's -1 stream index to the same file playback serves."""
    if idx >= 0:
        return idx
    from stremiosrv.api import playback
    return playback._guess(h, {})


def srt_to_vtt(text: str) -> str:
    """Convert a SubRip (.srt) body to WebVTT (browser <track>-compatible). Pass through if already
    WebVTT. Only difference that matters: SRT timestamps use a comma before milliseconds, VTT a dot."""
    text = text.lstrip("﻿")  # strip UTF-8 BOM
    if text.lstrip().startswith("WEBVTT"):
        return text
    text = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text)
    return "WEBVTT\n\n" + text


def to_webvtt(text: str) -> str:
    """Convert a fetched subtitle (SRT/ASS/SSA/VTT/SUB/...) to clean WebVTT via ffmpeg — the SAME path
    that already makes embedded subs render on strict players (ExoPlayer/VLC). The naive text
    conversion (srt_to_vtt) only handles well-formed SubRip; ASS/SSA (which OpenSubtitles serves a lot
    of) and CRLF/malformed SRT produce WebVTT that strict players reject (mpv tolerates it). Falls back
    to srt_to_vtt if ffmpeg is unavailable or can't parse the input."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sub", delete=False) as f:
            f.write(text)
            tmp = f.name
        proc = subprocess.run(
            # The input is our own local temp file, so ffmpeg never needs a network protocol. Pin the
            # whitelist to local ones so a subtitle body that is really a manifest can't make ffmpeg
            # fetch its segments (defence in depth: today ffmpeg's default already refuses http from a
            # file input, so this changes no working case -- it just makes that guarantee explicit).
            ["ffmpeg", "-hide_banner", "-y", "-protocol_whitelist", "file,crypto,data",
             "-i", tmp, "-f", "webvtt", "pipe:1"],
            capture_output=True, timeout=15, check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return srt_to_vtt(text)  # fallback: naive but charset-correct




_VTT_TEXT_CUE_RE = re.compile(
    r"(?m)^(?P<start>(?:\d{2}:)?\d{2}:\d{2}\.\d{3})(?P<arrow>[ \t]+-->[ \t]+)(?P<end>(?:\d{2}:)?\d{2}:\d{2}\.\d{3})(?P<settings>[^\r\n]*)"
)


def _vtt_seconds(value: str) -> float:
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        hours, minutes, seconds = "0", parts[0], parts[1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _vtt_clock(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, rem = divmod(millis, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def rebase_webvtt(text: str, offset_seconds: float) -> str:
    """Shift WebVTT cue clocks to the local HLS resume timeline."""
    offset = max(0.0, float(offset_seconds or 0.0))
    if offset <= 0:
        return text
    blocks = re.split(r"(\r?\n\r?\n)", text)
    out = []
    for block in blocks:
        match = _VTT_TEXT_CUE_RE.search(block)
        if match is None:
            out.append(block)
            continue
        end = _vtt_seconds(match.group("end"))
        if end <= offset:
            continue
        start = max(offset, _vtt_seconds(match.group("start")))
        replacement = (
            _vtt_clock(start - offset)
            + match.group("arrow")
            + _vtt_clock(end - offset)
            + match.group("settings")
        )
        out.append(block[:match.start()] + replacement + block[match.end():])
    return "".join(out)

# A browser User-Agent for outbound subtitle fetches. Subtitle CDNs — notably subs5.strem.io, which
# the OpenSubtitles addon serves through — return 403 to urllib's default "Python-urllib/x.y" agent;
# that 403 became our 502 "failed to fetch subtitle" -> blank subs. A normal browser UA is accepted.
_FETCH_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


@router.get("/subtitles.{ext}")
def subtitles_proxy(ext: str, request: Request, source: str = Query(alias="from"), startTime: int = 0) -> Response:
    """Fetch an external subtitle (Stremio passes `?from=<url>`) and serve it on our own origin —
    CORS-safe and format-normalized. Mirrors the stock server's `/subtitles.:ext`: **the client asks
    for the extension it wants.** Android/native players request **`.srt`** (SubRip, for ExoPlayer);
    the browser requests `.vtt`. A `.vtt` request is normalized to clean WebVTT via ffmpeg; any other
    ext is charset-decoded and served as SubRip.

    Two bugs this fixes (both blanked OpenSubtitles on Android): serving ONLY `/subtitles.vtt` meant
    the `.srt` request fell through nginx to the web-player index.html (player got HTML, not a cue);
    and the bare `urlopen` was 403'd by subs5.strem.io -> 502. See `_FETCH_UA`."""
    if not source.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="only http(s) subtitle sources are allowed")
    home = client.is_home_client(request)
    deadline = upstream.Deadline(upstream.DEADLINE)
    try:
        resp, conn = upstream.open_url(source, "GET", {"user-agent": _FETCH_UA}, home, deadline)
    except dest.Refused as e:
        logger.warning("subtitle source refused by destination guard: %s", e)
        raise HTTPException(status_code=403, detail="subtitle source not allowed") from e
    except Exception as e:  # unreachable, too slow, too many redirects, bad upstream
        raise HTTPException(status_code=502, detail="failed to fetch subtitle") from e
    try:
        raw = resp.read()
        content_encoding = resp.getheader("Content-Encoding", "") or ""
    except Exception as e:
        raise HTTPException(status_code=502, detail="failed to fetch subtitle") from e
    finally:
        resp.close()
        conn.close()
    text = decode_subtitle(_decompress(raw, content_encoding))
    if ext.lower() == "vtt":
        # charset=utf-8 so strict players (ExoPlayer) don't second-guess the encoding.
        return Response(content=rebase_webvtt(to_webvtt(text), max(0, min(int(startTime), 86400000)) / 1000.0), media_type="text/vtt; charset=utf-8")
    return Response(content=text, media_type="application/x-subrip; charset=utf-8")


def _ensure_edges(handle, idx: int, edge: int = 65536, timeout: float = 15.0) -> bool:
    """Make sure the first and last `edge` bytes of file `idx` are downloaded (the only bytes the
    OpenSubtitles hash reads), by boosting the covering pieces and waiting briefly."""
    size = handle.file_size(idx)
    if not size:
        return False
    plen = handle.piece_length()
    base = handle.file_offset(idx)
    spans = [(base, base + min(edge, size) - 1), (base + max(0, size - edge), base + size - 1)]
    pieces = sorted({p for lo, hi in spans for p in range(lo // plen, hi // plen + 1)})
    for p in pieces:
        handle.boost_piece(p, 0)
    end = time.time() + timeout
    while time.time() < end and not all(handle.have_piece(p) for p in pieces):
        time.sleep(0.2)
    return all(handle.have_piece(p) for p in pieces)


@router.get("/opensubHash")
def opensub_hash(request: Request, videoUrl: str | None = None, mediaURL: str | None = None) -> dict:
    """OpenSubtitles matching key, in the stock streaming-server envelope:
    `{"error": null, "result": {"size": <bytes>, "hash": "<16hex>"}}`.

    The OpenSubtitles addon queries by moviehash AND moviebytesize, so BOTH must be returned — a bare
    `{"result": "<hash>"}` (no size) silently breaks OpenSubtitles matching while other subtitle
    sources (IMDb/filename) still work. `result` is null when the file can't be resolved in time; the
    client then falls back to filename matching."""
    src = videoUrl or mediaURL
    if not src:
        raise HTTPException(status_code=422, detail="videoUrl or mediaURL required")
    parsed = parse_stream_url(src)
    eng = getattr(request.app.state, "engine", None)
    if parsed is not None and eng is not None:
        info_hash, idx = parsed
        h = eng.get(info_hash) or eng.add(info_hash)
        end = time.time() + 20
        while not h.has_metadata() and time.time() < end:
            time.sleep(0.2)
        idx = stream_file_idx(h, idx) if h.has_metadata() else -1
        if idx >= 0 and _ensure_edges(h, idx):
            hsh, size = opensubtitles_hash_and_size(file_disk_path(eng.save_path(), h, idx))
            return {"error": None, "result": {"size": size, "hash": hsh}}
        return {"error": None, "result": None}  # couldn't resolve in time -> client falls back to filename
    # No filesystem fallback: a real client sends our own /<ih>/<idx> stream URL (handled above) or
    # an addon http(s) URL, never a local path. Probing an arbitrary path for its size and hash is
    # a local-file oracle for anyone who can reach this route, so it is refused here -- the client
    # falls back to filename matching, exactly as it does for `result: null`.
    return {"error": None, "result": None}


@router.get("/subtitleSignature")
def subtitle_signature(videoUrl: str | None = None, container: str | None = None) -> dict:
    """Embedded-subtitle signature, in the stock envelope:
    `{"error": null, "result": {"signature": <string|null>}}`.

    stremio-video 0.0.93 (already the pin on stremio-web@development) calls this once per load whose
    probe does not rule out an embedded subtitle track, and puts the answer on
    `videoParams.embeddedSubtitleSignature`. `container` is the probe's `format.name`, sent by the
    client and accepted here to keep the declared contract complete; it is unused while the
    signature is null.

    **The signature is always null, and that is a decision, not a stub left behind.** There is
    nothing yet to compute against:

    * the reference implementation does not have this route. The published server.js v4.21.1
      (6.7 MB from dl.strem.io) contains zero occurrences of `subtitleSignature` and answers 404;
    * nothing consumes the value. Neither stremio-core nor stremio-web mentions it, and inside
      stremio-video it is only ever produced.

    So the algorithm is unspecified, and inventing one would be worse than returning nothing: the
    client accepts *any* string (`typeof signature === 'string'`), so a value we made up would be
    used the moment a consumer ships upstream — and wrong subtitle matching is a much harder bug to
    trace than absent subtitle matching. `null` is the client's own documented "nothing here" value,
    and the branch it already takes today.

    What this does buy over the 404 it replaces: the client's `fetch().then(resp.json())` no longer
    throws once per playback — on the player origin the SPA catch-all answered 200 with index.html,
    so it raised a JSON parse error rather than a clean 404 — and the ask is counted, which is the
    evidence for when implementing this properly becomes worth doing.

    Deliberately does NOT ffprobe to confirm "there are no subtitle tracks at all". probe_media()
    shells out uncached, and this is called at playback start on the same box that is serving the
    stream; a second 30s-timeout subprocess there buys a nicety and risks the thing that matters.
    """
    if not videoUrl:
        raise HTTPException(status_code=422, detail="videoUrl required")
    metrics.record_subtitle_signature()
    return {"error": None, "result": {"signature": None}}


@router.get("/{info_hash}/{idx:int}/subtitles.json")
def subtitles_list(info_hash: str, idx: int, mediaURL: str, request: Request) -> dict:
    # Unlike the playback routes, this one has an ordinary answer for "no tracks" and the player
    # asks for it on every playback. A slow probe must not turn that into a 500 -- but it is still
    # said out loud, because an empty list on a file that does have subtitles is otherwise silent.
    media = resolve_media_input(request, mediaURL)
    try:
        pr = probe_media(media)
    except ProbeTimeoutError:
        logger.warning("subtitle probe timed out; answering with no tracks")
        return {"subtitles": []}
    if "hls" in (pr.get("format", {}).get("name") or ""):
        raise HTTPException(status_code=415, detail="playlist inputs are not accepted")
    subs = [
        {"id": s.get("id"), "track": s.get("index"), "codec": s.get("codec"), "lang": s.get("lang")}
        for s in pr["streams"]
        if s.get("track") == "subtitle"
    ]
    return {"subtitles": subs}


def _subtitle_stream_by_global_index(media_url: str, track: int) -> dict:
    """Resolve the public `track` identifier returned by subtitles.json.

    probe_media() exposes FFmpeg's global stream index in both `id` and `index`. The old extractor
    incorrectly fed that value to `0:s:<n>`, where <n> is subtitle-relative. On files with many
    subtitle tracks this silently selected a different stream. Keep one public identifier contract:
    the value returned by subtitles.json is the exact value accepted by subtitles.vtt.
    """
    try:
        pr = probe_media(media_url)
    except ProbeTimeoutError as e:
        raise HTTPException(status_code=504, detail="subtitle probe timed out") from e

    for stream in pr.get("streams") or []:
        if stream.get("track") == "subtitle" and stream.get("index") == track:
            return stream
    raise HTTPException(status_code=404, detail="subtitle track not found")


def _webvtt_stream(proc: subprocess.Popen):
    """Yield FFmpeg WebVTT incrementally and always reap the child process.

    Full-track subtitle extraction may legitimately take longer than 60 seconds on a remote/torrent
    media URL. Streaming stdout avoids buffering the whole movie's subtitle track and removes the
    hard 60-second wall that caused HTTP 500 in 2.0.14.
    """
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(64 * 1024)
            if not chunk:
                break
            yield chunk
        rc = proc.wait()
        if rc:
            logger.warning("embedded subtitle ffmpeg exited with code %s", rc)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


_VTT_CUE_RE = re.compile(
    rb"(?m)^(?P<start>(?:\d{2}:)?\d{2}:\d{2}\.\d{3})[ \t]+-->[ \t]+"
    rb"(?P<end>(?:\d{2}:)?\d{2}:\d{2}\.\d{3})"
)


def _trace_webvtt_timeline(payload: bytes, start: float | None, duration: float | None) -> None:
    """Log timing metadata only: never subtitle text, media URL, hash or trackers."""
    cues = list(_VTT_CUE_RE.finditer(payload))
    first = cues[0].group("start").decode("ascii") if cues else "-"
    last = cues[-1].group("end").decode("ascii") if cues else "-"
    logger.debug(
        "subtitle trace: stage=vtt-result window_start=%s window_duration=%s bytes=%s cues=%s first=%s last=%s",
        f"{start:.3f}" if start is not None else "-",
        f"{duration:.3f}" if duration is not None else "-",
        len(payload),
        len(cues),
        first,
        last,
    )


def _add_webvtt_timestamp_map(payload: bytes, start: float | None, mpegts_start: int = 0, timeline_offset: float = 0.0) -> bytes:
    """Declare the HLS WebVTT clock for cues preserved on the media's absolute timeline."""
    if start is None or not payload.startswith(b"WEBVTT"):
        return payload
    # Window extraction uses -copyts, so FFmpeg's WebVTT cues remain on the media's absolute
    # timeline. Map LOCAL zero to MPEGTS zero; adding `start` here would shift every cue twice.
    local_ms = max(0, round(float(timeline_offset or 0.0) * 1000))
    local_h, rem = divmod(local_ms, 3600000)
    local_m, rem = divmod(rem, 60000)
    local_s, local_ms = divmod(rem, 1000)
    local_clock = f"{local_h:02d}:{local_m:02d}:{local_s:02d}.{local_ms:03d}"
    mapping = f"X-TIMESTAMP-MAP=LOCAL:{local_clock},MPEGTS:{max(0, int(mpegts_start))}\n".encode("ascii")
    head, sep, tail = payload.partition(b"\n")
    if not sep:
        return payload
    return head + sep + mapping + tail


def _read_webvtt_window(proc: subprocess.Popen, start: float | None, duration: float | None, mpegts_start: int = 0, timeline_offset: float = 0.0) -> bytes:
    """Collect one finite WebVTT window. Failed/empty output is deliberately not cacheable."""
    try:
        assert proc.stdout is not None
        try:
            if hasattr(proc, "communicate"):
                payload, _ = proc.communicate(timeout=_VTT_WINDOW_TIMEOUT)
            else:  # lightweight unit-test process doubles
                payload = proc.stdout.read()
                proc.wait(timeout=_VTT_WINDOW_TIMEOUT)
        except subprocess.TimeoutExpired:
            logger.warning("embedded subtitle ffmpeg exceeded %.0fs window timeout", _VTT_WINDOW_TIMEOUT)
            proc.kill()
            payload, _ = proc.communicate()
            return b""
        rc = getattr(proc, "returncode", None)
        if rc is None:
            rc = proc.poll()
        _trace_webvtt_timeline(payload, start, duration)
        payload = _add_webvtt_timestamp_map(payload, start, mpegts_start, timeline_offset)
        if rc:
            logger.warning("embedded subtitle ffmpeg exited with code %s", rc)
            return b""
        return payload
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def _cached_webvtt_window_stream(key: tuple, proc_factory, start: float | None, duration: float | None, mpegts_start: int = 0, timeline_offset: float = 0.0):
    """Deduplicate concurrent/retried HLS requests and reuse only completed VTT windows."""
    payload = _vtt_cache_get(key)
    if payload is None:
        with _vtt_window_lock(key):
            payload = _vtt_cache_get(key)
            if payload is None:
                payload = _read_webvtt_window(proc_factory(), start, duration, mpegts_start, timeline_offset)
                _vtt_cache_put(key, payload)
    if payload:
        yield payload


@router.get("/{info_hash}/-1/subtitles.vtt")
def subtitles_vtt_guessed(
    info_hash: str,
    mediaURL: str,
    request: Request,
    track: int = 0,
    start: float | None = None,
    duration: float | None = None,
    mpegtsStart: int = 0,
    timelineOffset: float = 0.0,
) -> StreamingResponse:
    """Serve WebVTT when stremio-core uses -1 for an implicit file index.

    Keep the public -1 contract intact. resolve_media_input(), used by
    subtitles_vtt(), resolves the mediaURL through the same GuessFileIdx
    semantics as normal playback.
    """
    return subtitles_vtt(
        info_hash=info_hash,
        idx=-1,
        mediaURL=mediaURL,
        request=request,
        track=track,
        start=start,
        duration=duration,
        mpegtsStart=mpegtsStart,
        **({"timelineOffset": timelineOffset} if timelineOffset else {}),
    )



@router.get("/{info_hash}/-1/subtitles.json")
def subtitles_list_guessed(info_hash: str, mediaURL: str, request: Request) -> dict:
    return subtitles_list(info_hash, -1, mediaURL, request)


@router.get("/{info_hash}/{idx:int}/subtitles.vtt")
def subtitles_vtt(
    info_hash: str,
    idx: int,
    mediaURL: str,
    request: Request,
    track: int = 0,
    start: float | None = None,
    duration: float | None = None,
    mpegtsStart: int = 0,
    timelineOffset: float = 0.0,
) -> StreamingResponse:
    media = resolve_media_input(request, mediaURL)

    # For our own active torrent, subtitle extraction uses the private auxiliary reader.
    # That reader yields piece deadlines to the viewer and does not count its waits as
    # playback stalls. Non-torrent inputs keep the normal guarded resolved URL.
    parsed = parse_stream_url(mediaURL)
    prefix_limit = None
    resolved_idx = idx
    if parsed is not None:
        try:
            from stremiosrv.api import embedded_ass
            resolved_idx = parsed[1]
            h = request.app.state.engine.get(parsed[0])
            if resolved_idx == -1 and h is not None and h.has_metadata():
                from stremiosrv.api.playback import _guess
                resolved_idx = _guess(h, {})
            if embedded_ass._playing(request, parsed[0], resolved_idx) is not None:
                prefix_limit = h.file_contiguous_prefix(resolved_idx)
                if prefix_limit > 0:
                    # Present the already-downloaded contiguous prefix as a finite virtual file.
                    # FFmpeg therefore sees EOF at the first torrent hole instead of blocking there.
                    media = embedded_ass.reader_url(
                        request, parsed[0], resolved_idx, limit=prefix_limit
                    )
                else:
                    media = embedded_ass.reader_url(request, parsed[0], resolved_idx)
        except Exception:
            logger.debug("subtitle finite-prefix reader unavailable", exc_info=True)

    # `track` is the global FFmpeg stream index returned by
    # /subtitles.json, not the subtitle-relative 0:s:<n> index.
    _subtitle_stream_by_global_index(media, track)
    logger.debug(
        "subtitle trace: stage=vtt method=%s track=%s windowed=%s",
        request.method,
        track,
        start is not None or duration is not None,
    )

    argv = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-protocol_whitelist",
        "file,crypto,data,http,tcp,tls,https",
    ]
    # Keep windowed subtitle timestamps on the media's absolute clock. This follows the
    # proven embedded-ASS extractor: seek on input with a small lead, preserve timestamps,
    # and stop at the absolute end of the requested window. Unlike output-side -t, -to with
    # -copyts also terminates correctly when subtitle packets are sparse.
    preroll = min(5.0, max(0.0, start or 0.0)) if start is not None else 0.0
    windowed = start is not None or duration is not None
    if windowed:
        argv += ["-copyts"]
    if start is not None:
        argv += ["-ss", f"{max(0.0, start - preroll):.3f}"]
    argv += ["-i", media]
    if duration is not None:
        window_start = max(0.0, start or 0.0)
        argv += ["-to", f"{window_start + max(0.001, duration):.3f}"]
    argv += [
        "-map", f"0:{track}",
        "-c:s", "webvtt",
        "-f", "webvtt",
        "pipe:1",
    ]

    def _start_proc():
        try:
            return subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except OSError as e:
            raise HTTPException(
                status_code=503,
                detail="subtitle extractor unavailable",
            ) from e

    if windowed:
        # The finite prefix is an extraction boundary, not subtitle identity. Once a window has
        # completed successfully, growth of the torrent prefix must not invalidate/re-run it.
        cache_key = (info_hash, resolved_idx, track, start, duration, mpegtsStart, timelineOffset)
        stream = _cached_webvtt_window_stream(cache_key, _start_proc, start, duration, mpegtsStart, timelineOffset)
    else:
        stream = _webvtt_stream(_start_proc())
    return StreamingResponse(
        stream,
        media_type="text/vtt; charset=utf-8",
    )
