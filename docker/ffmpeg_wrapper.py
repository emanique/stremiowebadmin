#!/usr/bin/env python3
"""Fork-owned FFmpeg execution-profile wrapper.

The Stremio core still decides whether a stream needs transcoding. This wrapper
only replaces the *video execution pipeline* when the operator selected one
explicit profile. Copy remains copy; no hidden codec choice and no silent
software fallback are introduced by the wrapper.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REAL_FFMPEG = os.getenv("FFMPEG_REAL", "/usr/local/libexec/stremio/ffmpeg-real")
REAL_FFPROBE = os.getenv("FFPROBE_REAL", "/usr/local/libexec/stremio/ffprobe-real")
CONFIG_FILE = os.getenv("STREMIOSRV_EXTERNAL_CONFIG", "/config/admin-settings.json")
VAAPI_DEVICE_DEFAULT = "/dev/dri/renderD128"

PROFILES = {
    "preserve": {"encoder": None, "engine": "core", "decode": "core", "label": "Preserve Stremio decision"},
    "vaapi-h264": {"encoder": "h264_vaapi", "engine": "vaapi", "decode": "software", "label": "H.264 VAAPI encode only"},
    "vaapi-hevc": {"encoder": "hevc_vaapi", "engine": "vaapi", "decode": "software", "label": "HEVC VAAPI encode only"},
    "vaapi-full-h264": {"encoder": "h264_vaapi", "engine": "vaapi", "decode": "vaapi", "label": "H.264 VAAPI full GPU"},
    "vaapi-full-hevc": {"encoder": "hevc_vaapi", "engine": "vaapi", "decode": "vaapi", "label": "HEVC VAAPI full GPU"},
    "nvenc-h264": {"encoder": "h264_nvenc", "engine": "nvenc", "decode": "software", "label": "H.264 NVENC"},
    "nvenc-hevc": {"encoder": "hevc_nvenc", "engine": "nvenc", "decode": "software", "label": "HEVC NVENC"},
    "cpu-h264": {"encoder": "libx264", "engine": "cpu", "decode": "software", "label": "H.264 CPU"},
    "cpu-hevc": {"encoder": "libx265", "engine": "cpu", "decode": "software", "label": "HEVC CPU"},
}


def _read_config() -> dict[str, object]:
    try:
        data = json.loads(Path(CONFIG_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _profile(config: dict[str, object]) -> str | None:
    value = str(
        config.get("transcoding_profile") or ""
    ).strip().lower()

    if value == "auto":
        resolved = str(
            config.get("transcoding_resolved_profile") or ""
        ).strip().lower()

        if resolved in PROFILES:
            return resolved

        # AUTO without a verified persisted resolution must not
        # guess an execution profile.
        return None

    return value if value in PROFILES else None


def _vaapi_device(config: dict[str, object]) -> str:
    return str(config.get("transcoding_vaapi_device") or os.getenv("VAAPI_DEVICE") or VAAPI_DEVICE_DEFAULT)


def _quality(config: dict[str, object]) -> int:
    try:
        return max(0, min(51, int(config.get("transcoding_video_quality", 22))))
    except (TypeError, ValueError):
        return 22


def _available_encoders() -> set[str]:
    try:
        result = subprocess.run(
            [REAL_FFMPEG, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return set(re.findall(r"^\s*[VAS][^\s]{5}\s+([^\s]+)", result.stdout, re.MULTILINE))


def _video_codec(args: list[str]) -> tuple[int | None, str | None]:
    for index, token in enumerate(args[:-1]):
        if token in {"-c:v", "-codec:v"}:
            return index, args[index + 1]
    return None, None


def _input_url(args: list[str]) -> str | None:
    """Return the first FFmpeg input following -i."""
    try:
        index = args.index("-i")
    except ValueError:
        return None
    if index + 1 >= len(args):
        return None
    return args[index + 1]


def _direct_video_codecs(config: dict[str, object]) -> set[str]:
    """Return normalized codecs allowed to remain Direct Stream."""
    value = str(
        config.get("transcoding_direct_video_codecs")
        or os.getenv("TRANSCODING_DIRECT_VIDEO_CODECS")
        or "h264"
    )
    aliases = {
        "avc": "h264",
        "avc1": "h264",
        "h265": "hevc",
        "x265": "hevc",
        "hev1": "hevc",
        "hvc1": "hevc",
    }
    result: set[str] = set()
    for item in value.split(","):
        codec = item.strip().lower()
        if codec:
            result.add(aliases.get(codec, codec))
    return result


def _probe_video_codec(args: list[str]) -> str | None:
    """Probe the first video stream codec without decoding video frames."""
    source = _input_url(args)
    if not source:
        return None

    aliases = {
        "avc": "h264",
        "avc1": "h264",
        "h265": "hevc",
        "x265": "hevc",
        "hev1": "hevc",
        "hvc1": "hevc",
    }

    try:
        result = subprocess.run(
            [
                REAL_FFPROBE,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=noprint_wrappers=1:nokey=1",
                source,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    codec = result.stdout.strip().splitlines()
    if not codec:
        return None

    value = codec[0].strip().lower()
    return aliases.get(value, value)



def _probe_video_format(args: list[str]) -> dict[str, str]:
    """Return source video characteristics used to decide safe VAAPI decode.

    The runtime self-test proves the GPU can decode representative H.264/HEVC,
    but real files can use profiles the iGPU decoder does not support. HEVC
    Main 10 was observed failing as "No support for codec hevc profile 2".
    """
    source = _input_url(args)
    if not source:
        return {}
    try:
        result = subprocess.run(
            [
                REAL_FFPROBE,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,profile,pix_fmt",
                "-of", "json",
                source,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return {}
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams") or []
        if not streams or not isinstance(streams[0], dict):
            return {}
        return {
            str(key): str(value)
            for key, value in streams[0].items()
            if value is not None
        }
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return {}


def _unsafe_full_vaapi_decode(details: dict[str, str]) -> bool:
    """Reject only source formats that the VAAPI path cannot safely classify.

    HEVC Main 10 is a normal VAAPI decode workload on the validated Intel/iHD
    backend.  3.0.8 treated every 10-bit HEVC source as unsafe and forced CPU
    decode, which made real-time HLS fall below 1x on production.  Keep the
    conservative fallback for high-bit-depth H.264 and unknown >10-bit HEVC;
    Main/Main10 HEVC may stay fully on the GPU.
    """
    codec = details.get("codec_name", "").lower()
    pix_fmt = details.get("pix_fmt", "").lower()
    profile = details.get("profile", "").lower()
    if codec == "hevc":
        return "12" in pix_fmt or "main 12" in profile
    if codec == "h264":
        return any(token in pix_fmt for token in ("10", "12", "p010")) or "high 10" in profile
    return False


def _remove_option(args: list[str], names: set[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(args):
        if args[index] in names and index + 1 < len(args):
            index += 2
            continue
        out.append(args[index])
        index += 1
    return out


def _replace_option(args: list[str], names: set[str], value: str) -> list[str]:
    out = list(args)
    for index, token in enumerate(out[:-1]):
        if token in names:
            out[index + 1] = value
            return out
    return out


def _insert_before_input(args: list[str], extra: list[str]) -> list[str]:
    if not extra:
        return args
    try:
        index = args.index("-i")
    except ValueError:
        return args
    return [*args[:index], *extra, *args[index:]]


def _extract_scale(filter_value: str | None) -> str | None:
    if not filter_value:
        return None
    match = re.search(r"(?:scale|scale_vaapi)(?:=w=|=)(\d+)", filter_value)
    return match.group(1) if match else None


def _existing_filter(args: list[str]) -> str | None:
    for index, token in enumerate(args[:-1]):
        if token in {"-vf", "-filter:v"}:
            return args[index + 1]
    return None


def _normalise_filter(args: list[str]) -> tuple[list[str], str | None]:
    old_filter = _existing_filter(args)
    width = _extract_scale(old_filter)
    result = _remove_option(args, {"-vf", "-filter:v"})
    return result, width


def _strip_encoder_tuning(args: list[str]) -> list[str]:
    return _remove_option(
        args,
        {
            "-preset", "-tune", "-crf", "-cq", "-qp", "-global_quality",
            "-rc", "-rc_mode", "-low_power",
            "-b:v", "-maxrate", "-minrate", "-bufsize",
            "-profile:v", "-level:v",
        },
    )


def _strip_hw_decode(args: list[str]) -> list[str]:
    return _remove_option(
        args,
        {"-hwaccel", "-hwaccel_device", "-hwaccel_output_format", "-vaapi_device"},
    )


def _has_explicit_hw_pipeline(args: list[str]) -> bool:
    explicit = {
        "-init_hw_device",
        "-filter_hw_device",
        "-hwaccel",
        "-hwaccel_device",
        "-hwaccel_output_format",
        "-vaapi_device",
    }
    return any(token in explicit for token in args)


def _insert_before_output_codec_options(args: list[str], extra: list[str]) -> list[str]:
    """Insert tuning/filter options immediately before -c:v."""
    for index, token in enumerate(args):
        if token in {"-c:v", "-codec:v"}:
            return [*args[:index], *extra, *args[index:]]
    return args


def _apply_profile(args: list[str], profile_name: str, config: dict[str, object]) -> tuple[list[str], str]:
    profile = PROFILES[profile_name]
    target = profile["encoder"]
    if target is None:
        return args, "profile=preserve; core decision unchanged"

    codec_index, current = _video_codec(args)
    if codec_index is None or current is None:
        return args, f"profile={profile_name}; no video codec option found"

    # When the core requests stream copy, validate the actual source codec
    # against the configured Direct Stream allow-list. Compatible codecs stay
    # untouched; incompatible codecs are passed through the selected execution
    # profile below.
    if current == "copy":
        source_codec = _probe_video_codec(args)
        direct_codecs = _direct_video_codecs(config)

        if source_codec is None:
            # Fail safe: never introduce unexpected transcoding when probing
            # cannot establish the input codec.
            return args, (
                f"profile={profile_name}; video=copy preserved; "
                "source codec probe unavailable"
            )

        if source_codec in direct_codecs:
            return args, (
                f"profile={profile_name}; video=copy preserved; "
                f"source={source_codec}; direct=yes"
            )

        current = f"copy({source_codec})"

    # Do not rewrite an already explicit hardware pipeline targeting the same
    # encoder. This protects manual diagnostics and upstream commands that have
    # deliberately constructed their own VAAPI/NVENC device/filter graph.
    if current == target and _has_explicit_hw_pipeline(args):
        return args, (
            f"profile={profile_name}; explicit hardware pipeline preserved; "
            f"encoder={current}"
        )

    encoders = _available_encoders()
    if target not in encoders:
        return args, f"profile={profile_name}; unavailable encoder {target}; core encoder {current} preserved"

    device = _vaapi_device(config)
    if profile["engine"] == "vaapi" and not os.path.exists(device):
        return args, f"profile={profile_name}; VAAPI device {device} unavailable; core encoder {current} preserved"

    quality = _quality(config)
    result = _strip_encoder_tuning(_strip_hw_decode(args))
    result, scale_width = _normalise_filter(result)
    result = _replace_option(result, {"-c:v", "-codec:v"}, str(target))

    effective_decode = str(profile["decode"])
    fallback_reason = ""
    if profile["engine"] == "vaapi" and effective_decode == "vaapi":
        details = _probe_video_format(args)
        if _unsafe_full_vaapi_decode(details):
            effective_decode = "software"
            fallback_reason = (
                f"; decode fallback=software"
                f" source={details.get('codec_name', 'unknown')}"
                f" profile={details.get('profile', 'unknown')}"
                f" pix_fmt={details.get('pix_fmt', 'unknown')}"
            )

    if profile["engine"] == "vaapi" and effective_decode == "vaapi":
        # Full VAAPI pipeline: decode directly to VAAPI surfaces and keep the
        # frames on the GPU through scale/format conversion and encode.
        result = _insert_before_input(
            result,
            [
                "-hwaccel", "vaapi",
                "-hwaccel_device", device,
                "-hwaccel_output_format", "vaapi",
            ],
        )
        vf = f"scale_vaapi=w={scale_width}:h=-2:format=nv12" if scale_width else "scale_vaapi=format=nv12"

        vaapi_options = ["-vf", vf]

        if str(target) == "h264_vaapi":
            vaapi_options += [
                "-low_power", "1",
                "-rc_mode", "CQP",
                "-qp", str(quality),
            ]
        else:
            vaapi_options += ["-qp", str(quality)]

        result = _insert_before_output_codec_options(result, vaapi_options)
    elif profile["engine"] == "vaapi":
        # Encode-only VAAPI: software decode, explicit upload to VAAPI for
        # hardware encode. Used explicitly by encode-only profiles and also as
        # a safe fallback when the real source profile is not decodable by VAAPI.
        result = _insert_before_input(result, ["-vaapi_device", device])
        software_filter = f"scale={scale_width}:-2:flags=lanczos" if scale_width else None
        vf = f"{software_filter},format=nv12,hwupload" if software_filter else "format=nv12,hwupload"

        vaapi_options = ["-vf", vf]

        # Intel Skylake/iHD exposes H.264 encode through the low-power
        # entrypoint with CQP rate control. This was runtime-verified on
        # /dev/dri/renderD129. Do not use bitrate control in this mode.
        if str(target) == "h264_vaapi":
            vaapi_options += [
                "-low_power", "1",
                "-rc_mode", "CQP",
                "-qp", str(quality),
            ]
        else:
            vaapi_options += ["-qp", str(quality)]

        result = _insert_before_output_codec_options(result, vaapi_options)
    elif profile["engine"] == "nvenc":
        if scale_width:
            result = _insert_before_output_codec_options(result, ["-vf", f"scale={scale_width}:-2:flags=lanczos"])
        result = _insert_before_output_codec_options(result, ["-preset", "p4", "-cq", str(quality)])
    else:
        if scale_width:
            result = _insert_before_output_codec_options(result, ["-vf", f"scale={scale_width}:-2:flags=lanczos"])
        result = _insert_before_output_codec_options(result, ["-preset", "veryfast", "-crf", str(quality)])

    effective_profile_name = profile_name
    if (
        profile["engine"] == "vaapi"
        and str(profile["decode"]) == "vaapi"
        and effective_decode == "software"
    ):
        # The requested full-GPU profile has deliberately fallen back to
        # software decode + VAAPI encode. Report the execution profile that
        # actually ran instead of advertising a full-GPU pipeline.
        effective_profile_name = (
            "vaapi-h264" if str(target) == "h264_vaapi" else "vaapi-hevc"
        )

    return result, (
        f"profile={effective_profile_name}; video={current}->{target}; "
        f"decode={effective_decode}; engine={profile['engine']}{fallback_reason}"
    )


def _legacy_passthrough(args: list[str], config: dict[str, object]) -> tuple[list[str], str]:
    """No new guess for installations that have not selected a profile yet."""
    legacy_mode = str(config.get("transcoding_mode") or "").strip().lower()
    if legacy_mode:
        return args, f"legacy settings detected ({legacy_mode}); select an explicit profile in WebAdmin"
    return args, "no explicit profile; core decision unchanged"




def _auto_apply(args: list[str], config: dict[str, object]) -> tuple[list[str], str]:
    """AUTO never changes Core video codec decisions.

    HLS master determines the browser's actual rendition and audio policy.
    A separate wrapper-level promotion of -c:v copy to VAAPI may silently
    introduce a hardware decode/filter graph unsupported by the source.
    Native App's direct Range responses never invoke FFmpeg at all.
    """
    _, current = _video_codec(args)
    if current == "copy":
        return args, "mode=auto; video=copy preserved; core decision authoritative"
    return args, "mode=auto; core decision unchanged"


def _runtime_env(args: list[str]) -> dict[str, str]:
    """Prepare only the environment required by an existing FFmpeg command.

    The wrapper does not choose codecs or force transcoding. If the command
    already uses VAAPI, ensure that libva has a driver. An explicit operator
    value always takes precedence.
    """
    env = os.environ.copy()

    uses_vaapi = (
        "-vaapi_device" in args
        or "h264_vaapi" in args
        or "hevc_vaapi" in args
    )

    if uses_vaapi and not str(
        env.get("LIBVA_DRIVER_NAME") or ""
    ).strip():
        env["LIBVA_DRIVER_NAME"] = "iHD"

    return env

def main() -> int:
    args = sys.argv[1:]

    if not os.path.exists(REAL_FFMPEG):
        print(
            f"[ffmpeg-policy] real ffmpeg not found: {REAL_FFMPEG}",
            file=sys.stderr,
        )
        return 127

    config = _read_config()
    mode = str(config.get("transcoding_mode") or "auto").strip().lower()

    effective_args = args
    if mode == "auto":
        effective_args, decision = _auto_apply(args, config)
    else:
        decision = f"mode={mode or 'unknown'}; core decision unchanged"

    print(f"[ffmpeg-policy] {decision}", file=sys.stderr)

    os.execve(
        REAL_FFMPEG,
        [REAL_FFMPEG, *effective_args],
        _runtime_env(effective_args),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
