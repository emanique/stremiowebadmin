"""Stremio hlsv2 transcode API: probe, master/media playlists, fMP4 segments.

The master playlist references child URIs we serve, so internal naming need not match the stock
server byte-for-byte — the player follows whatever URIs we publish.
"""
from __future__ import annotations

import ipaddress
import logging
import math
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from stremiosrv.api.media_fetch import _is_own_host, resolve_media_input
from stremiosrv.api.subs import parse_stream_url
from stremiosrv.transcode.converter import TranscodeRetryLimited, _direct_video_codecs
from stremiosrv.transcode.fingerprint import decide
from stremiosrv.transcode.probe import ProbeTimeoutError, probe_media

router = APIRouter(prefix="/hlsv2")
logger = logging.getLogger("stremiosrv.hls")

_M3U8 = "application/vnd.apple.mpegurl"


def _is_home_hls_client(request: Request) -> bool:
    from stremiosrv.proxy import client
    return client.is_home_client(request)


def _converter(request: Request):
    return getattr(request.app.state, "converter", None)


def _apply_hls_direct_codec_policy(decision: dict, source_video: dict,
                                   direct_video_codecs: set[str] | None = None) -> dict:
    """Apply the configured HLS copy whitelist in Core, not in FFmpeg wrapper.

    Native App byte-range playback is unaffected. This retains the original
    behaviour of converting e.g. HEVC to H.264 when only H.264 is permitted,
    but eliminates silent second-stage rewriting of the actual FFmpeg command.
    """
    video = decision.get("video") or {}
    if video.get("action") != "copy":
        return decision
    codec = str(source_video.get("codec") or "").strip().lower()
    allowed = _direct_video_codecs() if direct_video_codecs is None else direct_video_codecs
    if codec and codec not in allowed:
        video["action"] = "transcode"
        video["scale_width"] = int(source_video.get("width") or 1920)
        logger.info("HLS copy policy: codec=%s not direct-allowed -> Core transcode", codec)
    return decision


def _limit_4k_hevc_browser_transcode(decision: dict, source_video: dict, *, cap: int = 1920) -> dict:
    """Select compatible HLS transcoding for HEVC Main10 4K browser playback.

    Client codec declarations are insufficient to guarantee 10-bit HEVC MSE
    playback. More importantly, AUTO FFmpeg wrapper rewriting a Core COPY
    to VAAPI full transcoding can fail on Skylake HEVC Main10 decode.
    Make the Core the single video-policy authority: explicitly request
    encode-only VAAPI H.264 at <=1080p. Ordinary codecs, 8-bit HEVC,
    Direct file-stream and already smaller renditions are unaffected.
    """
    video = decision.get("video") or {}
    if (str(source_video.get("codec") or "").lower() != "hevc"
            or int(source_video.get("width") or 0) < 2560
            or int(source_video.get("bitDepth") or 8) < 10):
        return decision
    if video.get("action") not in {"copy", "transcode"}:
        return decision
    source_width = int(source_video.get("width") or cap)
    current_width = int(video.get("scale_width") or source_width)
    target_width = min(current_width, cap)
    if video.get("action") == "copy":
        video["action"] = "transcode"
        video["scale_width"] = target_width
        logger.info(
            "HLS browser compatibility: HEVC Main10 4K COPY -> H.264 transcode width=%d",
            target_width,
        )
    elif current_width > cap:
        video["scale_width"] = cap
        logger.info("HLS browser compatibility: HEVC Main10 transcode width capped at %d",cap)
    return decision


def _playback_registry(request: Request):
    return getattr(request.app.state, "playback_registry", None)


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback


def _iptv_compatibility_decision(
    decision: dict, probe: dict, media_url: str,
) -> tuple[dict, bool]:
    """Normalise external IPTV HLS/live MPEG-TS into H.264/AAC for browser and app clients.

    A source URL from the internal torrent endpoint is never intercepted. A
    multi-channel M3U catalog is not a playable source; callers must supply a
    selected channel URL. Network acquisition is still performed through the
    original guarded media-reader tickets rather than by FFmpeg directly.
    """
    source = probe.get("format") or {}
    name = str(source.get("name") or "").lower()
    try:
        duration = float(source.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    path = urlsplit(media_url).path.lower()
    is_hls = "hls" in name or path.endswith(".m3u8")
    is_live_ts = "mpegts" in name and duration <= 0
    host = (urlsplit(media_url).hostname or "").lower()
    # Internal torrent streams and loopback readers must not become IPTV jobs.
    is_iptv = (
        (is_hls or is_live_ts)
        and parse_stream_url(media_url) is None
        and not _is_loopback_host(host)
    )
    if not is_iptv:
        return decision, False
    if decision.get("video"):
        decision["video"]["action"] = "transcode"
        source_video = next(
            (s for s in probe.get("streams", []) if s.get("track") == "video"), {}
        )
        decision["video"]["scale_width"] = min(
            int(source_video.get("width") or 1920), 1920
        )
    if decision.get("audio"):
        decision["audio"]["action"] = "transcode"
    return decision, True


def _refuse_internal_hls_playlist(request: Request, media_url: str, pr: dict) -> None:
    """Retain the old torrent/internal-manifest 415 security contract."""
    fmt = str((pr.get("format") or {}).get("name") or "").lower()
    if "hls" not in fmt:
        return
    host = (urlsplit(media_url).hostname or "").lower()
    if _is_own_host(request, host) or _is_loopback_host(host):
        raise HTTPException(status_code=415, detail="internal playlist inputs are not accepted")


def _torrent_input_is_incomplete(request: Request, media_url: str) -> bool:
    """True only for a local torrent stream whose currently-wanted data is incomplete."""
    parsed = parse_stream_url(media_url)
    engine = getattr(request.app.state, "engine", None)
    if parsed is None or engine is None:
        return False
    try:
        handle = engine.get(parsed[0])
        return handle is not None and not handle.is_finished()
    except Exception:  # best-effort pacing hint; never changes playback admission
        return False


def _resolve_media_for_hls(request: Request, media_url: str) -> str:
    """Resolve an HLS input, retrying once when a torrent head is not ready yet.

    The retry deliberately calls resolve_media_input() again instead of bypassing the
    guard: each attempt must re-read and validate the torrent head before ffprobe is
    allowed to open it. Only the readiness-specific 504 is retried; 403/415 and every
    other error remain final.
    """
    try:
        return resolve_media_input(request, media_url)
    except HTTPException as exc:
        if exc.status_code != 504 or exc.detail != "media source too slow":
            raise
        logger.info("media source head not ready; retrying HLS preparation once")
        time.sleep(0.35)
        return resolve_media_input(request, media_url)


def _wait_file(path: Path, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(0.2)
    return path.exists()


def _subtitle_streams(probe: dict) -> list[dict]:
    return [
        stream for stream in (probe.get("streams") or [])
        if stream.get("track") == "subtitle" and stream.get("index") is not None
    ]


def _hls_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _master_with_subtitles(master_text: str, probe: dict, media_url: str, start_time_ms: int = 0) -> str:
    """Advertise embedded text subtitles in the HLS master without asking FFmpeg's HLS muxer to
    multiplex them.

    2.0.14 intentionally removed subtitle variants after FFmpeg rejected the old var_stream_map
    (notably the unsupported sname key). That made A/V reliable again, but Chromium's web player
    discovers embedded subtitles from the HLS master and never calls /subtitles.json on this path.
    We therefore keep FFmpeg responsible only for A/V and attach standards-based WebVTT rendition
    playlists at the API layer.
    """
    tracks = _subtitle_streams(probe)
    if not tracks:
        return master_text

    duration = float((probe.get("format") or {}).get("duration") or 0.0)
    media_lines: list[str] = []
    seen_names: dict[str, int] = {}
    for stream in tracks:
        track = int(stream["index"])
        lang = str(stream.get("lang") or "und")
        title = str(stream.get("title") or "").strip()
        qualifiers: list[str] = []
        if title:
            qualifiers.append(title)
        elif stream.get("forced"):
            qualifiers.append("Forced")
        elif stream.get("hearingImpaired"):
            qualifiers.append("Hearing Impaired")
        base_name = f"{lang} — {' / '.join(qualifiers)}" if qualifiers else lang
        seen_names[base_name] = seen_names.get(base_name, 0) + 1
        ordinal = seen_names[base_name]
        name = base_name if ordinal == 1 else f"{base_name} ({ordinal})"
        query = urlencode({"mediaURL": media_url, "duration": max(0.0, duration - start_time_ms / 1000.0), "startTime": max(0, int(start_time_ms))})
        uri = f"subtitles/{track}.m3u8?{query}"
        media_lines.append(
            '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",'
            f'NAME="{_hls_quote(name)}",LANGUAGE="{_hls_quote(lang)}",'
            f'AUTOSELECT=YES,DEFAULT={"YES" if stream.get("default") else "NO"},'
            f'FORCED={"YES" if stream.get("forced") else "NO"},URI="{_hls_quote(uri)}"'
        )

    out: list[str] = []
    inserted = False
    for line in master_text.splitlines():
        if line.startswith("#EXT-X-STREAM-INF:") and 'SUBTITLES=' not in line:
            if not inserted:
                out.extend(media_lines)
                inserted = True
            line += ',SUBTITLES="subs"'
        out.append(line)
    if not inserted:
        out.extend(media_lines)
    return "\n".join(out) + ("\n" if master_text.endswith("\n") else "")


def _subtitle_media_playlist(media_url: str, track: int, duration: float, mpegts_start: int = 0, start_time_ms: int = 0) -> str:
    """Build a VOD WebVTT media playlist with finite extraction windows."""
    parsed = parse_stream_url(media_url)
    if parsed is None:
        raise HTTPException(status_code=400, detail="subtitle mediaURL is not a server stream URL")
    info_hash, idx = parsed
    total = max(float(duration or 0.0), 0.001)
    segment = 30.0
    count = max(1, math.ceil(total / segment))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{math.ceil(segment)}",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]
    for n in range(count):
        offset = n * segment
        source_offset = max(0.0, start_time_ms / 1000.0) + offset
        length = min(segment, total - offset)
        query = urlencode({
            "mediaURL": media_url,
            "track": track,
            "start": f"{source_offset:.3f}",
            "duration": f"{length:.3f}",
            "timelineOffset": f"{max(0.0, start_time_ms / 1000.0):.3f}",
            "mpegtsStart": int(mpegts_start),
        })
        lines += [
            f"#EXTINF:{length:.3f},",
            f"/{info_hash}/{idx}/subtitles.vtt?{query}",
        ]
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


# HEAD is accepted on the read routes below. FastAPI, unlike bare Starlette, does NOT add HEAD to a
# GET route, so `@router.get` alone answers 405 — which is what the byte-range route already avoids
# by declaring both methods explicitly. Clients that probe a URL before playing it get a hard
# failure otherwise.
#
# /destroy is deliberately NOT in that set. It is the one route here whose GET has a side effect
# (it tears a transcode job down), and HEAD is defined as safe: a crawler, proxy or link-checker
# sending HEAD must not be able to kill someone's playback. It stays GET-only until the reference
# is shown to require otherwise.

# A probe that never answers used to escape as a 500 with a traceback. Both routes here need the
# probe to do their job, so they answer 504 -- the same gateway-timeout the playlist route already
# gives when a transcode fails to start.

@router.api_route("/probe", methods=["GET", "HEAD"])
def probe(mediaURL: str, request: Request) -> dict:
    media = _resolve_media_for_hls(request, mediaURL)
    try:
        pr = probe_media(media)
    except ProbeTimeoutError as e:
        raise HTTPException(status_code=504, detail="probe timed out") from e
    _refuse_internal_hls_playlist(request, mediaURL, pr)
    return pr


@router.api_route("/{job_id}/master.m3u8", methods=["GET", "HEAD"])
def master(
    job_id: str,
    request: Request,
    mediaURL: str | None = None,
    videoCodecs: list[str] = Query(default=[]),
    audioCodecs: list[str] = Query(default=[]),
    maxAudioChannels: int = 2,
    maxWidth: int = 3840,
    startTime: int = 0,
):
    conv = _converter(request)
    if conv is None:
        raise HTTPException(status_code=503, detail="transcoder unavailable")
    # Private IPTV sources are resolved exclusively on the server. Stremio
    # clients receive a signed channel job, never upstream credentials.
    signed_iptv = job_id.startswith("iptv-")
    if signed_iptv:
        import os

        from stremiosrv.iptv.store import enabled as iptv_enabled, resolve_job
        from stremiosrv.library import netguard
        if (os.environ.get("STREMIOSRV_IPTV_ENABLED", "").lower() not in ("1", "true", "yes")
                or not iptv_enabled()):
            raise HTTPException(status_code=404)
        peer = request.client.host if request.client else ""
        client_ip = netguard.client_ip(peer, request.headers.get("x-forwarded-for", ""))
        if not netguard.is_allowed(client_ip) or mediaURL is not None:
            raise HTTPException(status_code=404)
        channel = resolve_job(job_id)
        if channel is None:
            raise HTTPException(status_code=404)
        mediaURL = channel.url
    if not mediaURL:
        raise HTTPException(status_code=422, detail="mediaURL required")
    if signed_iptv:
        # The playlist is remotely managed and untrusted. Force internet-class
        # destination validation on every redirect and segment, even when the
        # viewer is in the LAN. Do not let the provider reach internal hosts.
        from urllib.parse import urlsplit

        from stremiosrv.api.media_fetch import reader_url, register
        from stremiosrv.proxy import dest
        target = urlsplit(mediaURL)
        try:
            dest.pick(target.hostname, target.port or (443 if target.scheme == "https" else 80), False)
        except (OSError, dest.Refused) as exc:
            raise HTTPException(status_code=502, detail="IPTV source unreachable") from exc
        media = reader_url(request, register(mediaURL, False))
    else:
        media = _resolve_media_for_hls(request, mediaURL)
    try:
        pr = probe_media(media)
    except ProbeTimeoutError as e:
        raise HTTPException(status_code=504, detail="probe timed out") from e
    _refuse_internal_hls_playlist(request, mediaURL, pr)
    dec = decide(pr, videoCodecs or ["h264"], audioCodecs or ["aac"], maxAudioChannels, maxWidth)
    source_video = next(
        (s for s in (pr.get("streams") or []) if s.get("track") == "video"),
        {},
    )
    logger.info(
        "hls decision: codec=%s profile=%s width=%s bitDepth=%s hdr=%s dovi=%s "
        "transfer=%s clientVideoCodecs=%s maxWidth=%s action=%s",
        source_video.get("codec"),
        source_video.get("profile"),
        source_video.get("width"),
        source_video.get("bitDepth"),
        bool(source_video.get("isHdr")),
        bool(source_video.get("isDoVi")),
        source_video.get("colorTransfer"),
        ",".join(videoCodecs or ["h264"]),
        maxWidth,
        (dec.get("video") or {}).get("action"),
    )
    # The Core exclusively owns HLS copy/transcode decisions.
    # Respect the deployment's codec allow-list *before* the 4K Main10 cap.
    _apply_hls_direct_codec_policy(dec, source_video)
    _limit_4k_hevc_browser_transcode(dec, source_video)
    # Browser and native app take the same compatibility path when they use
    # Core HLS. External IPTV must not retain codec COPY, even if the client
    # advertises that codec (ExoPlayer transitions can still fail).
    dec, iptv_compat = _iptv_compatibility_decision(dec, pr, mediaURL)
    if iptv_compat:
        logger.info("IPTV HLS compatibility: forcing H.264/AAC via Core")
    logging.getLogger("stremiosrv.transcode").info(
        "HLS request: job=%s sourceCodec=%s width=%s bitDepth=%s "
        "startTimeMs=%s maxWidth=%s outputAction=%s scaleWidth=%s "
        "clientVideoCodecs=%s clientAudioCodecs=%s",
        job_id[:48],
        source_video.get("codec"), source_video.get("width"),
        source_video.get("bitDepth"), startTime, maxWidth,
        (dec.get("video") or {}).get("action"),
        (dec.get("video") or {}).get("scale_width"),
        ",".join(videoCodecs or ["h264"]),
        ",".join(audioCodecs or ["aac"]),
    )
    # The fingerprint decision historically carried only the selected primary audio action. Preserve
    # the full probed stream inventory as private converter metadata so HLS can expose alternate
    # audio and text-subtitle renditions without changing the public fingerprint contract.
    dec["_streams"] = list(pr.get("streams") or [])
    dec["_startTimeMs"] = max(0, min(int(startTime), 86400000))
    # A hardware transcoder can consume an incomplete torrent much faster than playback time,
    # outrunning the swarm and repeatedly stalling on the next missing piece. Pace only that case.
    # Complete/cache-backed media keeps the normal faster-than-realtime HLS warm-up behaviour.
    dec["_realtimeInput"] = _torrent_input_is_incomplete(request, mediaURL)
    # External IPTV URLs can be infinite MPEG-TS byte streams (unlike a VOD
    # M3U playlist). They need a bounded sliding HLS window, not a growing
    # EVENT playlist. The source URI alone does not indicate its format.
    source_format = pr.get("format") or {}
    dec["_liveInput"] = (
        not dec["_realtimeInput"]
        and (
            (iptv_compat and float(source_format.get("duration") or 0) <= 0)
            or (
                "mpegts" in str(source_format.get("name") or "").lower()
                and float(source_format.get("duration") or 0) <= 0
            )
        )
    )
    try:
        d = conv.ensure_job(
            job_id, media, dec,
            source_identity=mediaURL,
            playback_intent={
                "startTimeMs": dec["_startTimeMs"],
                "videoCodecs": sorted(videoCodecs or ["h264"]),
                "audioCodecs": sorted(audioCodecs or ["aac"]),
                "maxAudioChannels": maxAudioChannels,
                "maxWidth": maxWidth,
                # Keep a home-authorised source distinct from a public one,
                # even when the URL string is identical.
                "homeClient": _is_home_hls_client(request),
            },
        )
    except TranscodeRetryLimited as e:
        raise HTTPException(
            status_code=503, detail=str(e), headers={"Retry-After": "60"}
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid job id") from e
    registry = _playback_registry(request)
    if signed_iptv:
        from stremiosrv.iptv import switches
        client_ip = netguard.client_ip(
            request.client.host if request.client else "",
            request.headers.get("x-forwarded-for", ""),
        )
        previous = switches.switch(client_ip, job_id)
        if previous:
            conv.stop(previous)
            if registry is not None:
                registry.end_hls(previous)
    parsed_source = parse_stream_url(mediaURL)
    if parsed_source is not None:
        try:
            from stremiosrv.library.labels import record_played_source
            eng = request.app.state.engine
            handle = eng.get(parsed_source[0]) if eng else None
            torrent_file = getattr(handle, 'torrent_file', None)
            ti = torrent_file() if callable(torrent_file) else None
            if ti and ti.files().num_files() == 1:
                record_played_source(request.app.state.settings.cache_root,
                                     parsed_source[0], parsed_source[1])
        except (OSError, ValueError, TypeError):
            pass  # Never interrupt HLS when a preference cannot be saved.
    if registry is not None:
        # Non-torrent HLS jobs must also be visible for workload and cleanup
        # monitoring; only torrents carry a verified infohash/file index.
        registry.register_hls_job(
            job_id,
            parsed_source[0] if parsed_source is not None else "",
            parsed_source[1] if parsed_source is not None else -1,
            request.headers.get("User-Agent"),
            conv.workload_for_job(job_id),
        )
    master_path = d / "master.m3u8"
    if not _wait_file(master_path, 25):
        raise HTTPException(status_code=504, detail="transcode did not start")
    try:
        master_text = master_path.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail="failed to read master playlist") from e
    body = _master_with_subtitles(master_text, pr, mediaURL, dec["_startTimeMs"])
    # The EVENT media playlist grows while FFmpeg is transcoding. Expose the immutable source
    # duration on variant URLs so the Web Player can report the real movie duration to Core
    # without treating the currently generated HLS window as the end of the title.
    source_duration_ms = max(0, round(float((pr.get("format") or {}).get("duration") or 0.0) * 1000))
    if source_duration_ms:
        lines = []
        for line in body.splitlines():
            if line and not line.startswith("#") and ".m3u8" in line:
                sep = "&" if "?" in line else "?"
                line = f"{line}{sep}sourceDuration={source_duration_ms}"
            lines.append(line)
        body = "\n".join(lines) + ("\n" if body.endswith("\n") else "")
    tracks = _subtitle_streams(pr)
    logger.debug(
        "subtitle trace: stage=master method=%s tracks=%s advertised=%s",
        request.method,
        len(tracks),
        body.count("#EXT-X-MEDIA:TYPE=SUBTITLES"),
    )
    return Response(content=body, media_type=_M3U8)


def _hls_mpegts_start(conv, job_id: str) -> int:
    """Return the MPEG-TS clock at HLS timeline zero (90 kHz), derived from this workload."""
    try:
        master = conv.job_file(job_id, "master.m3u8")
        if not _wait_file(master, 5):
            return 0
        variants = [line.strip() for line in master.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")]
        if not variants:
            return 0
        playlist = conv.job_file(job_id, variants[0])
        if not _wait_file(playlist, 5):
            return 0
        segments = [line.strip() for line in playlist.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")]
        if not segments:
            return 0
        segment = conv.job_file(job_id, segments[0])
        if not _wait_file(segment, 5):
            return 0
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=start_time",
             "-of", "default=nw=1:nk=1", str(segment)],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if proc.returncode != 0:
            return 0
        seconds = float(proc.stdout.strip())
        return max(0, round(seconds * 90000))
    except (OSError, ValueError, subprocess.SubprocessError):
        logger.debug("unable to derive HLS MPEG-TS start", exc_info=True)
        return 0


@router.api_route("/{job_id}/subtitles/{track:int}.m3u8", methods=["GET", "HEAD"])
def subtitle_playlist(job_id: str, track: int, request: Request, mediaURL: str, duration: float = 0.0, startTime: int = 0):
    conv = _converter(request)
    if conv is None:
        raise HTTPException(status_code=503, detail="transcoder unavailable")
    # Mark subtitle playlist traffic as playback activity so the A/V transcode is not reaped while a
    # browser is actively consuming the subtitle rendition.
    conv.touch(job_id)
    mpegts_start = _hls_mpegts_start(conv, job_id)
    body = _subtitle_media_playlist(mediaURL, track, duration, mpegts_start, max(0, min(int(startTime), 86400000)))
    logger.debug(
        "subtitle trace: stage=playlist method=%s track=%s",
        request.method,
        track,
    )
    return Response(content=body, media_type=_M3U8)


@router.get("/{job_id}/destroy")
def destroy(job_id: str, request: Request) -> dict:
    conv = _converter(request)
    if conv is not None:
        try:
            conv.stop(job_id)
        except ValueError as e:
            # This route deletes a directory, so a malformed id is refused rather than ignored.
            raise HTTPException(status_code=400, detail="invalid job id") from e
    registry = _playback_registry(request)
    if registry is not None:
        registry.end_hls(job_id)
    return {"ok": True}


@router.api_route("/{job_id}/{filename}", methods=["GET", "HEAD"])
def serve_file(job_id: str, filename: str, request: Request):
    conv = _converter(request)
    if conv is None:
        raise HTTPException(status_code=503, detail="transcoder unavailable")
    try:
        path = conv.job_file(job_id, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid job path") from e
    # A request for a segment or a playlist is the only evidence this server ever gets that anyone
    # is still watching: ffmpeg keeps encoding whether or not the output is being read. Recorded
    # before the wait below, so a client blocked on a segment that has not been written yet still
    # counts as present.
    conv.touch(job_id)
    if job_id.startswith("iptv-"):
        from stremiosrv.iptv import switches
        from stremiosrv.library import netguard
        client_ip = netguard.client_ip(
            request.client.host if request.client else "",
            request.headers.get("x-forwarded-for", ""),
        )
        switches.touch(client_ip, job_id)
    registry = _playback_registry(request)
    if registry is not None:
        registry.touch_hls(job_id, request.headers.get("User-Agent"))
    is_playlist = filename.endswith(".m3u8")
    if not _wait_file(path, 25 if is_playlist else 35):
        raise HTTPException(status_code=404, detail="segment not found")
    if is_playlist:
        return FileResponse(path, media_type=_M3U8)
    if filename.endswith(".vtt"):
        media_type = "text/vtt"
    elif filename.endswith(".ts"):
        media_type = "video/mp2t"
    elif filename.endswith(".mp4"):
        media_type = "video/mp4"
    else:
        media_type = "video/iso.segment"
    return FileResponse(path, media_type=media_type)
