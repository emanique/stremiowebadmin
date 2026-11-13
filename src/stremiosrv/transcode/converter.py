"""On-the-fly HLS transcoder: one ffmpeg job per request id, fMP4 segments on disk.

`build_hls_cmd` is pure (unit-testable); `Converter` manages the ffmpeg subprocess lifecycle.
Uses an EVENT playlist so the player can start after the first segment instead of waiting for the
whole transcode (full VOD-on-demand seeking is a later refinement).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from stremiosrv import metrics
from stremiosrv.transcode.profiler import detect_backend

logger = logging.getLogger("stremiosrv.transcode")


def workload_key(media_url: str, decision: dict, profile: str | None, backend: str | None = None) -> str:
    """Stable identity of the actual HLS encoding workload.

    job_id identifies a client/session.  It must not be used to decide whether
    two requests require the same encoder.
    """
    payload = {
        "media_url": media_url,
        "decision": decision,
        "profile": profile,
        "backend": backend,
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _hls_audio_tracks(decision: dict) -> list[dict]:
    streams = decision.get("_streams") or []
    return [s for s in streams if s.get("track") == "audio" and s.get("index") is not None][:10]


def _hls_lang(track: dict) -> str | None:
    value = str(track.get("lang") or "").strip()
    if not value:
        return None
    # var_stream_map is a comma/space-delimited mini-language. Keep metadata from becoming syntax.
    cleaned = "".join(ch for ch in value if ch.isalnum() or ch in "_-")
    return cleaned[:24] or None


def _direct_video_codecs() -> set[str]:
    """Video codecs allowed to remain Direct Stream.

    The client fingerprint may report video=copy because the player declared
    support for the source codec.  The server execution policy is stricter:
    AUTO/full transcoding profiles may intentionally restrict Direct Stream
    to TRANSCODING_DIRECT_VIDEO_CODECS.
    """
    raw = os.environ.get("TRANSCODING_DIRECT_VIDEO_CODECS", "h264")
    return {
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    }


def _source_video_stream(decision: dict) -> dict:
    """Return probed source-video metadata carried privately by the HLS decision."""
    return next(
        (
            stream
            for stream in decision.get("_streams") or []
            if stream.get("track") == "video"
        ),
        {},
    )


def _source_video_codec(decision: dict) -> str | None:
    """Return the probed source video codec already carried by the HLS decision."""
    codec = str(_source_video_stream(decision).get("codec") or "").strip().lower()
    return codec or None


def _source_is_hdr(decision: dict) -> bool:
    """Whether the source carries HDR/PQ/HLG video.

    This is source classification only. It never promotes COPY to TRANSCODE.
    Dolby Vision may also carry a PQ base layer and therefore remains HDR for
    the purpose of the validated software SDR fallback.
    """
    source = _source_video_stream(decision)
    transfer = str(source.get("colorTransfer") or "").strip().lower()
    return bool(source.get("isHdr")) or transfer in {"smpte2084", "arib-std-b67"}


def _apply_direct_video_policy(
    decision: dict,
    profile: str | None,
) -> dict:
    """Preserve the Stremio core video decision.

    AUTO-only hardware discovery must never promote Direct Stream/copy
    into transcoding. Hardware availability is a capability, not a codec
    or playback policy.
    """
    return decision



def _write_single_track_master(out_dir: str | Path) -> Path:
    """Write the stable master playlist used by single-track browser HLS.

    FFmpeg owns index.m3u8, init.mp4 and the media segments. The application
    owns this small master so the public /master.m3u8 contract never depends
    on FFmpeg generating a variant playlist without -var_stream_map.
    """
    path = Path(out_dir) / "master.m3u8"
    tmp = path.with_suffix(".m3u8.tmp")
    tmp.write_text(
        "#EXTM3U\n"
        "#EXT-X-VERSION:7\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000\n"
        "index.m3u8\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path

def build_hls_cmd(
    media_url: str,
    decision: dict,
    profile: str | None,
    out_dir: str | Path,
    backend: dict[str, str | None] | None = None,
) -> list[str]:
    out_dir = str(out_dir)

    decision = _apply_direct_video_policy(decision, profile)

    v = decision.get("video", {})
    a = decision.get("audio")
    argv = ["ffmpeg", "-hide_banner", "-y",
            "-protocol_whitelist", "file,crypto,data,http,tcp,tls,https"]

    # AUTO separates playback policy from execution capability. Hardware is
    # consulted only after the Stremio core explicitly requests transcoding.
    auto_backend = backend or (detect_backend() if profile == "auto" else None)
    backend_name = str((auto_backend or {}).get("backend") or "none")
    backend_device = (auto_backend or {}).get("device")

    audio_tracks = _hls_audio_tracks(decision)
    multitrack = len(audio_tracks) > 1

    # Retain legacy explicit-profile behaviour for upgrade compatibility.
    # AUTO never enters these branches.
    if v.get("action") == "transcode":
        if profile == "nvenc-linux":
            argv += ["-hwaccel", "cuda"]
        elif profile and profile.startswith("vaapi-full-"):
            argv += ["-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi"]

    source_hdr = _source_is_hdr(decision)
    auto_vaapi_hdr = (
        v.get("action") == "transcode"
        and profile == "auto"
        and backend_name == "vaapi"
        and bool(backend_device)
        and source_hdr
    )
    # Ordinary AUTO VAAPI remains encode-only. HDR is different: once the
    # core has already requested a video transcode, keeping HDR10/PQ frames
    # on the VAAPI surface allows tonemap_vaapi to produce SDR BT.709 in real
    # time. Hardware availability still never promotes COPY to TRANSCODE.
    if v.get("action") == "transcode" and profile == "auto" and backend_name == "vaapi" and backend_device:
        argv += ["-vaapi_device", str(backend_device)]
        if auto_vaapi_hdr:
            source = _source_video_stream(decision)
            color_primaries = str(source.get("colorPrimaries") or "").strip()
            color_transfer = str(source.get("colorTransfer") or "").strip()
            color_space = str(source.get("colorSpace") or "").strip()

            argv += [
                "-hwaccel", "vaapi",
                "-hwaccel_device", str(backend_device),
                "-hwaccel_output_format", "vaapi",
            ]

            # Some HDR/Dolby Vision streams expose BT.2020/PQ signalling but
            # no mastering-display side data.  Jellyfin FFmpeg 4.4.1
            # tonemap_vaapi can still process those streams in real time when
            # the known source colour properties are supplied explicitly.
            if color_primaries:
                argv += ["-color_primaries", color_primaries]
            if color_transfer:
                argv += ["-color_trc", color_transfer]
            if color_space:
                argv += ["-colorspace", color_space]

    start_time_ms = max(0, int(decision.get("_startTimeMs") or 0))
    if start_time_ms:
        argv += ["-ss", f"{start_time_ms / 1000:.3f}"]
    if decision.get("_realtimeInput"):
        argv += ["-re"]
    argv += ["-i", media_url, "-map", "0:v:0"]
    if multitrack:
        for track in audio_tracks:
            argv += ["-map", f"0:{track['index']}?"]
    elif a is not None:
        argv += ["-map", "0:a:0?"]

    # Video. COPY is never promoted to transcoding by AUTO.
    if v.get("action") == "copy":
        argv += ["-c:v", "copy"]
    else:
        w = v.get("scale_width")
        if profile == "auto" and backend_name == "vaapi" and backend_device:
            if auto_vaapi_hdr:
                source_width = int(_source_video_stream(decision).get("width") or 0)

                filters = [
                    "tonemap_vaapi=format=nv12:matrix=bt709:primaries=bt709:transfer=bt709"
                ]
                if w and (not source_width or int(w) < source_width):
                    filters.append(f"scale_vaapi=w={w}:h=-2:format=nv12")

                vf = ",".join(filters)
            else:
                vf = f"scale={w}:-2:flags=lanczos,format=nv12,hwupload" if w else "format=nv12,hwupload"
            argv += ["-vf", vf, "-c:v", "h264_vaapi"]
        elif profile == "auto" and backend_name == "nvenc":
            # NVENC encode only: AUTO does not force CUDA/NVDEC decode.
            vf = f"scale={w}:-2:flags=lanczos,format=yuv420p" if w else "format=yuv420p"
            argv += ["-vf", vf, "-c:v", "h264_nvenc", "-preset", "p4"]
        elif profile == "nvenc-linux":
            argv += ["-vf", f"scale={w}:-2:flags=lanczos,format=yuv420p" if w else "format=yuv420p",
                     "-c:v", "h264_nvenc", "-preset", "p4"]
        elif profile and profile.startswith("vaapi-full-"):
            vf = f"scale_vaapi=w={w}:h=-2:format=nv12" if w else "scale_vaapi=format=nv12"
            argv += ["-vf", vf, "-c:v", "h264_vaapi"]
        elif profile and profile.startswith("vaapi"):
            vf = f"scale={w}:-2:flags=lanczos,format=nv12,hwupload" if w else "format=nv12,hwupload"
            argv += ["-vf", vf, "-c:v", "h264_vaapi"]
        else:
            if w:
                argv += ["-vf", f"scale={w}:-2:flags=lanczos"]
            argv += ["-c:v", "libx264", "-preset", "veryfast"]

    # Audio. A multi-track HLS presentation normalises every rendition to AAC stereo so the
    # browser can switch tracks reliably even when the source mixes E-AC3/DTS/TrueHD/etc. The old
    # single-track path keeps its copy/transcode decision unchanged.
    if multitrack and audio_tracks:
        argv += ["-c:a", "aac", "-ac", "2", "-b:a", "192k"]
    elif a is not None:
        if a.get("action") == "copy":
            argv += ["-c:a", "copy"]
            # MPEG-TS commonly carries AAC in ADTS framing. fMP4 HLS
            # requires AudioSpecificConfig; a bitstream filter converts
            # headers without re-encoding the audio.
            source_audios = [
                item for item in decision.get("_streams", [])
                if item.get("track") == "audio"
            ]
            if (not multitrack and decision.get("_liveInput")
                    and source_audios and source_audios[0].get("codec") == "aac"):
                argv += ["-bsf:a", "aac_adtstoasc"]
        else:
            argv += ["-c:a", "aac", "-ac", "2", "-ab", "192000"]

    if multitrack:
        variants: list[str] = []
        for index, track in enumerate(audio_tracks):
            item = f"a:{index},agroup:audio,default:{'yes' if index == 0 else 'no'}"
            lang = _hls_lang(track)
            if lang:
                item += f",language:{lang}"
            variants.append(item)
        video = "v:0,agroup:audio"
        variants.append(video)

        # FFmpeg reliably supports alternate audio as separate HLS renditions. Embedded subtitles
        # deliberately stay on Stremio's native /subtitles.json + /subtitles.vtt path: FFmpeg HLS
        # cannot represent subtitle-only variants, and doing so caused 2.0.13 to abort before
        # writing master.m3u8. Keeping subtitle delivery separate also preserves the player's
        # existing language/track discovery contract.
        argv += [
            "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "event",
            "-hls_segment_type", "mpegts", "-hls_flags", "independent_segments",
            "-hls_segment_filename", f"{out_dir}/seg_%v_%d.ts",
            "-var_stream_map", " ".join(variants),
            "-master_pl_name", "master.m3u8", f"{out_dir}/stream_%v.m3u8",
        ]
    else:
        # Single-track HLS uses a deterministic application-owned master.
        # For live MPEG-TS, maintain a sliding playlist and delete old
        # segments; EVENT mode would grow forever and is only for VOD.
        playlist_args = (
            ["-hls_list_size", "8", "-hls_flags",
             "delete_segments+independent_segments"]
            if decision.get("_liveInput")
            else ["-hls_playlist_type", "event", "-hls_flags",
                  "independent_segments"]
        )
        argv += [
            "-f", "hls", "-hls_time", "4", *playlist_args,
            "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", "init.mp4",
            "-hls_segment_filename", f"{out_dir}/seg%d.m4s",
            f"{out_dir}/index.m3u8",
        ]
    return argv


def _ffmpeg_failure_summary(stderr_tail: str) -> str:
    """Keep the causal FFmpeg error, not merely its generic final line."""
    text = stderr_tail.replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    markers = ("error", "failed", "invalid", "could not", "unable", "unknown",
               "out of memory", "input/output", "end of file", "not supported",
               "not found", "device busy", "timed out")
    causes = [
        line for line in lines
        if any(term in line.lower() for term in markers)
        and line.lower() not in {"conversion failed!", "error while filtering: invalid argument"}
    ]
    return " | ".join(causes[-3:])[-700:] if causes else (lines[-1][:280] if lines else "no stderr")


class TranscodeRetryLimited(RuntimeError):
    """A failing HLS workload has exceeded its bounded restart budget."""


class Converter:
    """Manage shared HLS encoding workloads.

    ``job_id`` identifies a client/playback session. Multiple job ids may
    reference the same encoding workload. The actual ffmpeg process and HLS
    output directory are therefore owned by ``workload_key`` rather than by
    the client job id.
    """

    STOP_TIMEOUT = 5.0

    def __init__(self, cache_root: str, profile: str | None):
        self.base = Path(cache_root) / "transcode"
        self.profile = profile
        self.backend = detect_backend() if profile == "auto" else None

        # Compatibility note:
        # _jobs now means workload-key -> ffmpeg process.
        self._jobs: dict[str, subprocess.Popen] = {}

        # Client job id -> workload key.
        self._job_workload: dict[str, str] = {}

        # Workload key -> client job ids currently referencing it.
        self._workload_jobs: dict[str, set[str]] = {}

        # Workload key -> last observed client activity.
        self._seen: dict[str, float] = {}
        # A playlist is refreshed frequently. Failed processes must not be
        # restarted without bound while the source/codec remains unavailable.
        self._failed_restarts: dict[str, list[float]] = {}

        self._lock = threading.Lock()

    def job_dir(self, job_id: str) -> Path:
        """Validate a client job id and return its nominal path.

        The nominal path is retained as the validation chokepoint. Shared HLS
        output itself is stored in a workload-key directory.
        """
        if not job_id or job_id in (".", "..") or "/" in job_id or chr(92) in job_id:
            raise ValueError(f"unsafe transcode job id: {job_id!r}")

        candidate = self.base / job_id
        try:
            if candidate.resolve().parent != self.base.resolve():
                raise ValueError(f"unsafe transcode job id: {job_id!r}")
        except OSError as e:
            raise ValueError(f"unresolvable transcode job id: {job_id!r}") from e

        return candidate

    def _workload_dir(self, key: str) -> Path:
        # workload_key is a SHA-256 hex digest generated internally.
        if (
            len(key) != 64
            or any(ch not in "0123456789abcdef" for ch in key)
        ):
            raise ValueError("invalid transcode workload key")

        return self.base / key

    def _dir_for_job_locked(self, job_id: str) -> Path:
        key = self._job_workload.get(job_id)
        if key is None:
            # Preserve validation behaviour for unknown job ids.
            return self.job_dir(job_id)
        return self._workload_dir(key)

    def job_file(self, job_id: str, filename: str) -> Path:
        if not filename or filename in (".", "..") or "/" in filename or chr(92) in filename:
            raise ValueError(f"unsafe transcode file name: {filename!r}")

        # Validate job id even when it currently has no alias.
        self.job_dir(job_id)

        with self._lock:
            d = self._dir_for_job_locked(job_id)

        candidate = d / filename
        try:
            if candidate.resolve().parent != d.resolve():
                raise ValueError(f"unsafe transcode file name: {filename!r}")
        except OSError as e:
            raise ValueError(f"unresolvable transcode file name: {filename!r}") from e

        return candidate

    def _remove_workload_dir(self, key: str) -> None:
        try:
            d = self._workload_dir(key)
        except ValueError:
            return
        shutil.rmtree(d, ignore_errors=True)

    def _remove_job_dir(self, job_id: str) -> None:
        """Remove legacy per-job output left by older releases."""
        try:
            d = self.job_dir(job_id)
        except ValueError:
            return
        shutil.rmtree(d, ignore_errors=True)

    def ensure_job(
        self,
        job_id: str,
        media_url: str,
        decision: dict,
        *,
        source_identity: str | None = None,
        playback_intent: dict | None = None,
    ) -> Path:
        """Return the shared HLS output directory for a client job.

        ``job_id`` is a client/session alias. The actual ffmpeg process is
        owned by the deterministic workload key.

        Lookup, alias registration and process creation happen under one lock
        so concurrent requests for the same workload cannot launch duplicate
        encoders. An old workload made ownerless by rebinding is stopped only
        after leaving the lock.
        """
        # Validate the untrusted URL component before storing it anywhere.
        self.job_dir(job_id)

        # The guarded external media reader uses a fresh random ticket on
        # every request. It is an execution capability, not the media identity.
        # The original URL + explicit playback parameters remain stable across
        # M3U8 master refreshes, even if ffprobe metadata fluctuates.
        if source_identity is not None and playback_intent is None:
            raise ValueError("source identity requires explicit playback intent")
        key = workload_key(
            source_identity if source_identity is not None else media_url,
            playback_intent if playback_intent is not None else decision,
            self.profile,
            str((self.backend or {}).get("backend") or "none"),
        )

        logger.info(
            "transcode request: job=%s workload=%s profile=%s",
            job_id,
            key[:16],
            self.profile or "none",
        )

        orphaned_old_key: str | None = None

        with self._lock:
            # Keep the crash budget across alias removal and idle GC.
            # A playlist player may retry GET /master.m3u8 indefinitely.
            failures = [t for t in self._failed_restarts.get(key, [])
                        if time.monotonic() - t < 60]
            if failures:
                self._failed_restarts[key] = failures
            else:
                self._failed_restarts.pop(key, None)
            if len(failures) >= 2:
                raise TranscodeRetryLimited("transcode restart limit reached")
            old_key = self._job_workload.get(job_id)

            # Same alias, same workload: ordinary master.m3u8 refresh.
            if old_key == key:
                existing = self._jobs.get(key)

                if existing is not None and existing.poll() is None:
                    self._seen[key] = time.monotonic()
                    return self._workload_dir(key)

                # Repeated HLS playlist polling must not restart a crashing
                # FFmpeg indefinitely. Count failed exits in a rolling window.
                if existing is not None and existing.poll() not in (None, 0):
                    failures = [t for t in self._failed_restarts.get(key, [])
                                if time.monotonic() - t < 60]
                    failures.append(time.monotonic())
                    self._failed_restarts[key] = failures
                    if len(failures) >= 2:
                        logger.warning(
                            "transcode retry budget exhausted: workload=%s failures=%d/60s",
                            key[:16], len(failures),
                        )
                        raise TranscodeRetryLimited("transcode restart limit reached")
                # The previous encoder exited. Remove stale process state;
                # the alias remains and will be rebound to the replacement
                # process below.
                self._jobs.pop(key, None)
                self._seen.pop(key, None)

            # Same client alias is now asking for a different effective
            # output. Detach it from the previous workload.
            elif old_key is not None:
                owners = self._workload_jobs.get(old_key)

                if owners is not None:
                    owners.discard(job_id)

                    if not owners:
                        self._workload_jobs.pop(old_key, None)
                        orphaned_old_key = old_key

                self._job_workload.pop(job_id, None)

            existing = self._jobs.get(key)

            if existing is not None and existing.poll() is None:
                # Another client already owns the exact same encoder output.
                self._job_workload[job_id] = key
                self._workload_jobs.setdefault(key, set()).add(job_id)
                self._seen[key] = time.monotonic()

                result = self._workload_dir(key)

                logger.info(
                    "transcode reuse: job=%s workload=%s owners=%d",
                    job_id,
                    key[:16],
                    len(self._workload_jobs[key]),
                )

            else:
                # A dead process may still have bookkeeping under this key.
                if existing is not None:
                    self._jobs.pop(key, None)
                    self._seen.pop(key, None)

                d = self._workload_dir(key)

                # No live process owns this directory now. Remove partial
                # output from a previous failed/exited encoder before reuse.
                shutil.rmtree(d, ignore_errors=True)
                d.mkdir(parents=True, exist_ok=True)

                argv = build_hls_cmd(
                    media_url,
                    decision,
                    self.profile,
                    d,
                    self.backend,
                )

                # Multi-track HLS lets FFmpeg build the master from
                # -var_stream_map. Single-track HLS has no variant map, so
                # publish the application-owned master before FFmpeg starts.
                if len(_hls_audio_tracks(decision)) <= 1:
                    _write_single_track_master(d)

                log = open(d / "ffmpeg.log", "wb")  # noqa: SIM115

                proc = subprocess.Popen(
                    argv,
                    stdout=subprocess.DEVNULL,
                    stderr=log,
                )

                self._jobs[key] = proc
                self._job_workload[job_id] = key
                self._workload_jobs[key] = {job_id}
                self._seen[key] = time.monotonic()

                metrics.record_hls_session(decision)

                result = d

        # Never call _stop_workload while holding self._lock.
        #
        # Re-check ownership inside _stop_workload_if_orphaned because another
        # thread may have attached itself to the old workload between the
        # unlock above and this cleanup.
        if orphaned_old_key is not None:
            self._stop_workload_if_orphaned(orphaned_old_key)

        return result

    def workload_for_job(self, job_id: str) -> str | None:
        """Return the encoder workload identity currently backing a client job."""
        with self._lock:
            return self._job_workload.get(job_id)

    def touch(self, job_id: str) -> None:
        """Record activity for the shared workload referenced by job_id."""
        with self._lock:
            key = self._job_workload.get(job_id)
            if key is not None and key in self._jobs:
                self._seen[key] = time.monotonic()

    def reap_idle(self, idle_after: float) -> list[str]:
        """End workloads that remained idle at the moment they are detached.

        Selection and detachment are atomic with respect to touch()/reuse().
        Process termination and filesystem deletion happen after releasing
        the registry lock.
        """
        now = time.monotonic()
        detached: list[
            tuple[str, subprocess.Popen, list[str]]
        ] = []

        with self._lock:
            for key, proc in list(self._jobs.items()):
                exited = proc.poll() is not None
                last_seen = self._seen.get(key, 0.0)

                # poll() reaps an exited child, but its registry/filesystem
                # bookkeeping must also be detached. Otherwise dead encoders
                # remain addressable forever and stale output can be reused.
                if not exited and now - last_seen < idle_after:
                    continue

                # Detach atomically. Once removed from the registry, a new
                # request cannot attach to this process; it will create a
                # replacement workload instead.
                proc = self._jobs.pop(key)
                aliases = list(
                    self._workload_jobs.pop(key, set())
                )
                self._seen.pop(key, None)

                for job_id in aliases:
                    if self._job_workload.get(job_id) == key:
                        self._job_workload.pop(job_id, None)

                detached.append((key, proc, aliases))

        reaped: list[str] = []

        for key, proc, aliases in detached:
            rc = proc.poll()
            if rc not in (None, 0):
                with self._lock:
                    t = time.monotonic()
                    recent = [x for x in self._failed_restarts.get(key, [])
                              if t - x < 60]
                    recent.append(t)
                    self._failed_restarts[key] = recent
            if rc is not None:
                # Distinguish failed/exited encoders from genuinely idle clients.
                # Capture only a bounded diagnostic tail before deleting output.
                log_path = self._workload_dir(key) / "ffmpeg.log"
                try:
                    with log_path.open("rb") as fp:
                        fp.seek(0, os.SEEK_END)
                        fp.seek(max(0, fp.tell() - 12288))
                        tail = fp.read().decode("utf-8", errors="replace")
                    diagnostic = _ffmpeg_failure_summary(tail)
                except OSError:
                    diagnostic = "no ffmpeg log"
                logger.warning(
                    "transcode ffmpeg exited: workload=%s returncode=%s last_output=%s",
                    key[:16], rc, diagnostic,
                )
            else:
                logger.info(
                    "transcode gc: no client for %.0fs, ending workload %s",
                    idle_after, key[:16],
                )

            self._end(proc)
            self._remove_workload_dir(key)

            for job_id in aliases:
                self._remove_job_dir(job_id)

            reaped.extend(aliases or [key])

        return reaped

    def active_count(self) -> int:
        """Number of unique ffmpeg encoding workloads currently running."""
        with self._lock:
            return sum(
                1
                for p in self._jobs.values()
                if p.poll() is None
            )

    def _end(self, p: subprocess.Popen | None) -> None:
        if p is None or p.poll() is not None:
            return

        p.terminate()

        try:
            p.wait(timeout=self.STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            p.kill()
            # A killed child remains a zombie until its parent reaps it.
            # Always wait after kill so long-lived Uvicorn workers do not
            # accumulate defunct ffmpeg processes.
            p.wait()

    def _stop_workload_if_orphaned(self, key: str) -> bool:
        """Stop key only if no alias acquired it after the caller unlocked.

        Rebinding has to release ``self._lock`` before process termination.
        Another request can legitimately attach to the old workload in that
        interval, so ownership must be checked again here.
        """
        with self._lock:
            owners = self._workload_jobs.get(key)

            if owners:
                return False

            p = self._jobs.pop(key, None)
            self._seen.pop(key, None)
            self._workload_jobs.pop(key, None)

            stale_aliases = [
                job_id
                for job_id, workload in self._job_workload.items()
                if workload == key
            ]

            for job_id in stale_aliases:
                self._job_workload.pop(job_id, None)

        self._end(p)
        self._remove_workload_dir(key)

        for job_id in stale_aliases:
            self._remove_job_dir(job_id)

        return True

    def _stop_workload(self, key: str) -> None:
        with self._lock:
            p = self._jobs.pop(key, None)
            aliases = self._workload_jobs.pop(key, set())
            self._seen.pop(key, None)

            for job_id in aliases:
                if self._job_workload.get(job_id) == key:
                    self._job_workload.pop(job_id, None)

        self._end(p)
        self._remove_workload_dir(key)

        # Also clean legacy directories if this workload inherited aliases
        # from a previous release.
        for job_id in aliases:
            self._remove_job_dir(job_id)

    def stop(self, job_id: str) -> None:
        """Release one client alias.

        The shared encoder is terminated only when the final owner releases
        the workload.
        """
        # Preserve path validation for /destroy.
        self.job_dir(job_id)

        key = None
        terminate = False

        with self._lock:
            key = self._job_workload.pop(job_id, None)

            if key is not None:
                owners = self._workload_jobs.get(key)

                if owners is not None:
                    owners.discard(job_id)

                    if not owners:
                        self._workload_jobs.pop(key, None)
                        terminate = True

        # Legacy output from releases that used job_id as the directory.
        self._remove_job_dir(job_id)

        if key is not None and terminate:
            self._stop_workload(key)

    def stop_all(self) -> None:
        with self._lock:
            jobs = list(self._jobs.items())
            aliases = list(self._job_workload)
            keys = list(self._jobs)

            self._jobs.clear()
            self._job_workload.clear()
            self._workload_jobs.clear()
            self._seen.clear()

        for _, p in jobs:
            self._end(p)

        for key in keys:
            self._remove_workload_dir(key)

        for job_id in aliases:
            self._remove_job_dir(job_id)

    def sweep(self, max_age: float = 0.0) -> int:
        """Delete directories that no live shared workload owns."""
        try:
            names = os.listdir(self.base)
        except OSError:
            return 0

        with self._lock:
            live = {
                key
                for key, p in self._jobs.items()
                if p.poll() is None
            }

        now = time.time()
        removed: list[str] = []

        for name in names:
            if name in live:
                continue

            d = self.base / name

            try:
                if not d.is_dir():
                    continue

                if max(0.0, now - d.stat().st_mtime) < max_age:
                    continue
            except OSError:
                continue

            shutil.rmtree(d, ignore_errors=True)

            if not d.exists():
                removed.append(name)

        if removed:
            logger.info(
                "transcode gc: reclaimed %d orphaned job dir(s)",
                len(removed),
            )

        return len(removed)

def run_transcode_gc(converter: Converter, interval: int = 60, max_age: int = 600,
                     idle_after: int = 300, on_reaped=None) -> None:
    """Background loop reclaiming abandoned transcodes and their output. Runs forever.

    Two reclaims, in order. `reap_idle` ends encoders nothing is reading -- a crashed player holds
    a GPU otherwise -- and `sweep` deletes directories no encoder owns. The reap comes first so a
    job it just ended is eligible for the sweep in the same pass rather than the next one.
    """
    if not logger.handlers:  # match the evictor: uvicorn doesn't surface our INFO logs by default
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s [transcode] %(message)s"))
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    while True:
        time.sleep(interval)
        try:
            reaped = converter.reap_idle(idle_after)
            if on_reaped is not None:
                for job_id in reaped:
                    try:
                        on_reaped(job_id)
                    except Exception:
                        logger.exception('transcode playback registry cleanup failed for %s', job_id)
        except Exception:
            logger.exception("transcode reap failed")
        try:
            converter.sweep(max_age=max_age)
        except Exception:
            logger.exception("transcode gc pass failed")
