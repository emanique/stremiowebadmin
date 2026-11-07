"""Simple, runtime-verified transcoding profiles for WebAdmin.

Profiles are offered only after real FFmpeg tests in the running streaming
server container. Compiled encoder names are never treated as proof that a GPU
pipeline is usable.
"""
from __future__ import annotations

import re
import shlex
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

import transcoding_runtime_fix as base

app = base.app
legacy = base.base.legacy
STATIC = Path(__file__).with_name("static")
PROFILE_CACHE: dict[str, object] = {"at": 0.0, "value": None}

PROFILE_META = {
    "preserve": {
        "label": "Preserve Stremio decision",
        "encoder": None,
        "engine": "core",
        "decode": "core",
        "codec": "unchanged",
        "description": "No encoder override. Stremio keeps full control.",
    },
    "vaapi-h264": {
        "label": "H.264 VAAPI — GPU encode only",
        "encoder": "h264_vaapi",
        "engine": "vaapi",
        "decode": "software",
        "codec": "H.264",
        "description": "Software decode, Intel/DRM VAAPI H.264 encode. Direct Stream remains direct.",
    },
    "vaapi-hevc": {
        "label": "HEVC VAAPI — GPU encode only",
        "encoder": "hevc_vaapi",
        "engine": "vaapi",
        "decode": "software",
        "codec": "HEVC",
        "description": "Software decode, Intel/DRM VAAPI HEVC encode. Direct Stream remains direct.",
    },
    "vaapi-full-h264": {
        "label": "H.264 VAAPI — Full GPU",
        "encoder": "h264_vaapi",
        "engine": "vaapi",
        "decode": "vaapi",
        "codec": "H.264",
        "description": "VAAPI hardware decode and H.264 hardware encode; frames remain on the GPU.",
    },
    "vaapi-full-hevc": {
        "label": "HEVC VAAPI — Full GPU",
        "encoder": "hevc_vaapi",
        "engine": "vaapi",
        "decode": "vaapi",
        "codec": "HEVC",
        "description": "VAAPI hardware decode and HEVC hardware encode; frames remain on the GPU.",
    },
    "nvenc-h264": {
        "label": "H.264 NVIDIA NVENC",
        "encoder": "h264_nvenc",
        "engine": "nvenc",
        "decode": "software",
        "codec": "H.264",
        "description": "Software decode, NVIDIA NVENC H.264 encode capability; diagnostic only.",
    },
    "nvenc-hevc": {
        "label": "HEVC NVIDIA NVENC",
        "encoder": "hevc_nvenc",
        "engine": "nvenc",
        "decode": "software",
        "codec": "HEVC",
        "description": "Software decode, NVIDIA NVENC HEVC encode capability; diagnostic only.",
    },
    "cpu-h264": {
        "label": "H.264 CPU (libx264)",
        "encoder": "libx264",
        "engine": "cpu",
        "decode": "software",
        "codec": "H.264",
        "description": "Software decode and libx264 encode.",
    },
    "cpu-hevc": {
        "label": "HEVC CPU (libx265)",
        "encoder": "libx265",
        "engine": "cpu",
        "decode": "software",
        "codec": "HEVC",
        "description": "Software decode and libx265 encode.",
    },
}


class ProfileBody(BaseModel):
    profile: str
    quality: int = Field(default=22, ge=0, le=51)


def _exec(container, argv: list[str]):
    try:
        return container.exec_run(argv)
    except Exception:
        return None


def _selected() -> tuple[str, int]:
    """AUTO is the only supported execution mode."""
    persisted = legacy.read_config() or {}

    try:
        quality = int(
            persisted.get("transcoding_video_quality")
            or 22
        )
    except (TypeError, ValueError):
        quality = 22

    quality = max(0, min(51, quality))

    return "auto", quality


def _result(
    profile_id: str,
    available: bool,
    reason: str,
    verified: bool = True,
    **details: object,
) -> dict[str, object]:
    return {
        **PROFILE_META[profile_id],
        "id": profile_id,
        "available": available,
        "verified": verified,
        "reason": reason,
        **details,
    }


def _result_text(result) -> str:
    if result is None or not result.output:
        return ""
    return result.output.decode("utf-8", errors="replace").strip()


def _nvenc_diagnostics(result) -> dict[str, object]:
    raw = _result_text(result)
    required = None
    found = None
    match = re.search(
        r"required nvenc API version\.\s*Required:\s*([0-9.]+)\s*Found:\s*([0-9.]+)",
        raw,
        re.IGNORECASE,
    )
    if match:
        required, found = match.groups()
        return {
            "failureCode": "nvenc-api-incompatible",
            "nvencApiCompatible": False,
            "nvencApiRequired": required,
            "nvencApiAvailable": found,
            "reason": (
                f"NVIDIA detected and runtime ready, but NVENC API is incompatible: "
                f"required {required}, available {found}."
            ),
        }

    return {
        "failureCode": "encoder-self-test-failed",
        "nvencApiCompatible": None,
        "nvencApiRequired": None,
        "nvencApiAvailable": None,
        "reason": _last_error(result),
    }


def _libva_driver(container) -> str:
    """Return the VAAPI driver used for runtime hardware self-tests.

    The server container is authoritative.  An explicitly configured
    LIBVA_DRIVER_NAME wins; Intel/DRM deployments default to iHD.
    """
    try:
        env = base.base._container_env(container)
    except Exception:
        env = {}

    driver = str(env.get("LIBVA_DRIVER_NAME") or "").strip()
    return driver or "iHD"


def _last_error(result) -> str:
    if result is None or not result.output:
        return "runtime self-test failed"
    raw = result.output.decode("utf-8", errors="replace").strip()
    return raw.splitlines()[-1][:220] if raw else "runtime self-test failed"


def _test_full_vaapi(container, profile_id: str, device: str, binary: str) -> dict[str, object]:
    """Verify both H.264 and HEVC VAAPI decode followed by the selected VAAPI encoder."""
    encoder = str(PROFILE_META[profile_id]["encoder"])
    driver = _libva_driver(container)
    b = shlex.quote(binary)
    d = shlex.quote(device)
    e = shlex.quote(encoder)
    drv = shlex.quote(driver)
    script = f"""
set -eu
export LIBVA_DRIVER_NAME={drv}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# H.264 8-bit
{b} -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=128x72:rate=24 \
  -frames:v 3 \
  -c:v libx264 -preset ultrafast \
  -pix_fmt yuv420p \
  "$work/h264.mp4"

# HEVC Main 8-bit
{b} -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=128x72:rate=24 \
  -frames:v 3 \
  -c:v libx265 -preset ultrafast \
  -pix_fmt yuv420p \
  -x265-params log-level=error \
  "$work/hevc-main.mkv"

# HEVC Main10 10-bit
{b} -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=128x72:rate=24 \
  -frames:v 3 \
  -vf format=yuv420p10le \
  -c:v libx265 -preset ultrafast \
  -profile:v main10 \
  -x265-params log-level=error \
  "$work/hevc-main10.mkv"

for input in \
  "$work/h264.mp4" \
  "$work/hevc-main.mkv" \
  "$work/hevc-main10.mkv"
do
  {b} -hide_banner -loglevel error \
    -hwaccel vaapi \
    -hwaccel_device {d} \
    -hwaccel_output_format vaapi \
    -i "$input" \
    -frames:v 1 \
    -vf scale_vaapi=format=nv12 \
    -c:v {e} \
    -f null -
done
"""
    result = _exec(container, ["sh", "-lc", script])
    ok = bool(result is not None and result.exit_code == 0)
    if ok:
        return _result(
            profile_id,
            True,
            "Full GPU test passed: H.264 + HEVC Main + HEVC Main10 VAAPI decode and VAAPI encode.",
        )
    return _result(profile_id, False, _last_error(result))


def _test_profile(container, profile_id: str, device: str, binary: str | None) -> dict[str, object]:
    meta = PROFILE_META[profile_id]
    if profile_id == "preserve":
        return _result(profile_id, True, "No encoder required.")
    if not binary:
        return _result(profile_id, False, "FFmpeg binary not available.", verified=False)

    encoder = str(meta["encoder"])
    if meta["engine"] == "vaapi":
        if not base._exists(container, device):
            return _result(profile_id, False, f"{device} is not mounted.")
        if meta["decode"] == "vaapi":
            return _test_full_vaapi(container, profile_id, device, binary)
        driver = _libva_driver(container)
        argv = [
            "env", f"LIBVA_DRIVER_NAME={driver}",
            binary, "-hide_banner", "-loglevel", "error",
            "-vaapi_device", device,
            "-f", "lavfi", "-i", "color=c=black:s=128x72:d=0.04",
            "-vf", "format=nv12,hwupload", "-frames:v", "1",
            "-c:v", encoder, "-f", "null", "-",
        ]

        result = _exec(container, argv)

        # Some Intel generations, notably Skylake/iHD, expose H.264 encode
        # through the low-power entrypoint with CQP only. Retry with the
        # runtime-proven low-power CQP path before marking VAAPI unavailable.
        if (
            profile_id == "vaapi-h264"
            and (result is None or result.exit_code != 0)
        ):
            argv = [
                "env", f"LIBVA_DRIVER_NAME={driver}",
                binary, "-hide_banner", "-loglevel", "error",
                "-vaapi_device", device,
                "-f", "lavfi", "-i", "color=c=black:s=128x72:d=0.04",
                "-vf", "format=nv12,hwupload", "-frames:v", "1",
                "-c:v", encoder,
                "-low_power", "1",
                "-rc_mode", "CQP",
                "-qp", "23",
                "-f", "null", "-",
            ]
            result = _exec(container, argv)

        ok = bool(result is not None and result.exit_code == 0)
        if ok:
            reason = (
                f"VAAPI {PROFILE_META[profile_id]['codec']} runtime self-test passed. "
                "Low-power/CQP fallback is supported for H.264 where required."
            )
        else:
            reason = _last_error(result)
        return _result(profile_id, ok, reason)

    else:
        argv = [
            binary, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=128x72:d=0.04",
            "-frames:v", "1", "-c:v", encoder, "-f", "null", "-",
        ]

    result = _exec(container, argv)
    ok = bool(result is not None and result.exit_code == 0)
    if ok:
        reason = "One-frame encoder runtime self-test passed."
        if meta["engine"] == "nvenc":
            return _result(
                profile_id,
                True,
                reason,
                nvencApiCompatible=True,
                failureCode=None,
            )
    else:
        if meta["engine"] == "nvenc":
            diagnostics = _nvenc_diagnostics(result)
            reason = str(diagnostics.pop("reason"))
            return _result(profile_id, False, reason, **diagnostics)
        reason = _last_error(result)
    return _result(profile_id, ok, reason)


def _recommended_profile(
    items: list[dict[str, object]],
) -> str:
    """Return verified hardware backend for diagnostics only.

    This value is not an execution-profile selection.
    """
    available = {
        str(item.get("id"))
        for item in items
        if item.get("available")
        and item.get("verified", True)
    }

    if "nvenc-h264" in available or "nvenc-hevc" in available:
        return "nvenc"

    if "vaapi-h264" in available or "vaapi-hevc" in available:
        return "vaapi"

    return "none"


def _nvidia_runtime_info(container) -> dict[str, object]:
    detected = base._exists(container, "/dev/nvidia0")
    name = None
    driver = None
    runtime = False

    if detected:
        result = _exec(
            container,
            [
                "sh", "-lc",
                "command -v nvidia-smi >/dev/null 2>&1 && "
                "nvidia-smi --query-gpu=name,driver_version "
                "--format=csv,noheader 2>/dev/null | head -n 1 || true"
            ],
        )
        if result is not None and result.exit_code == 0:
            line = result.output.decode("utf-8", errors="replace").strip()
            if line:
                parts = [item.strip() for item in line.split(",", 1)]
                name = parts[0] or None
                driver = parts[1] if len(parts) > 1 else None
                runtime = True

    return {
        "detected": detected,
        "runtime": runtime,
        "name": name,
        "driver": driver,
    }


def _backend_matrix(
    container,
    items: list[dict[str, object]],
    device: str,
) -> dict[str, object]:
    profiles = {str(item.get("id")): item for item in items}

    def available(profile_id: str) -> bool:
        item = profiles.get(profile_id) or {}
        return bool(item.get("available"))

    def reason(profile_id: str) -> str:
        item = profiles.get(profile_id) or {}
        return str(item.get("reason") or "")

    vaapi_device = base._exists(container, device)
    nvidia = _nvidia_runtime_info(container)

    vaapi_h264 = available("vaapi-h264")
    vaapi_hevc = available("vaapi-hevc")
    nvenc_h264 = available("nvenc-h264")
    nvenc_hevc = available("nvenc-hevc")
    nvenc_h264_item = profiles.get("nvenc-h264") or {}
    nvenc_hevc_item = profiles.get("nvenc-hevc") or {}
    nvenc_api_required = (
        nvenc_h264_item.get("nvencApiRequired")
        or nvenc_hevc_item.get("nvencApiRequired")
    )
    nvenc_api_available = (
        nvenc_h264_item.get("nvencApiAvailable")
        or nvenc_hevc_item.get("nvencApiAvailable")
    )
    nvenc_api_compatible = None
    for nvenc_item in (nvenc_h264_item, nvenc_hevc_item):
        if nvenc_item.get("nvencApiCompatible") is False:
            nvenc_api_compatible = False
            break
        if nvenc_item.get("nvencApiCompatible") is True:
            nvenc_api_compatible = True
    cpu_h264 = available("cpu-h264")
    cpu_hevc = available("cpu-hevc")

    return {
        "vaapi": {
            "id": "vaapi",
            "label": "Intel / DRM VAAPI",
            "detected": vaapi_device,
            "runtime": vaapi_device,
            "device": device if vaapi_device else None,
            "h264": vaapi_h264,
            "hevc": vaapi_hevc,
            "availableForAuto": vaapi_h264 or vaapi_hevc,
            "reason": (
                reason("vaapi-h264")
                if not vaapi_h264
                else "Runtime-verified VAAPI encoder available."
            ),
        },
        "nvidia": {
            "id": "nvidia",
            "label": nvidia.get("name") or "NVIDIA GPU",
            "detected": bool(nvidia.get("detected")),
            "runtime": bool(nvidia.get("runtime")),
            "driver": nvidia.get("driver"),
            "h264": nvenc_h264,
            "hevc": nvenc_hevc,
            "availableForAuto": nvenc_h264 or nvenc_hevc,
            "nvencApiCompatible": nvenc_api_compatible,
            "nvencApiRequired": nvenc_api_required,
            "nvencApiAvailable": nvenc_api_available,
            "reason": (
                reason("nvenc-h264")
                if not nvenc_h264
                else "Runtime-verified NVENC encoder available."
            ),
        },
        "cpu": {
            "id": "cpu",
            "label": "CPU software encoding",
            "detected": True,
            "runtime": True,
            "h264": cpu_h264,
            "hevc": cpu_hevc,
            "availableForAuto": cpu_h264 or cpu_hevc,
            "reason": (
                reason("cpu-h264")
                if not cpu_h264
                else "Runtime-verified software encoder available."
            ),
        },
    }


def _hardware_detection(items: list[dict[str, object]], device: str) -> dict[str, object]:
    available = {str(item.get("id")) for item in items if item.get("available")}
    if "nvenc-h264" in available or "nvenc-hevc" in available:
        accelerator = "nvenc"
        label = "NVIDIA NVENC"
        detected = True
    elif any(profile_id.startswith("vaapi-") for profile_id in available):
        accelerator = "vaapi"
        label = "VAAPI GPU (Intel/AMD DRM)"
        detected = True
    else:
        accelerator = "cpu"
        label = "CPU software encoding"
        detected = False

    return {
        "detected": detected,
        "accelerator": accelerator,
        "label": label,
        "device": device if accelerator == "vaapi" else None,
        "lxcCompatible": accelerator == "vaapi",
    }


def _profiles(force: bool = False) -> dict[str, object]:
    now = time.monotonic()
    container = legacy.client().containers.get(legacy.CONTAINER)
    # The Stremio container can be recreated independently by the host-side
    # gateway port reconciler. Never reuse GPU capabilities from a previous
    # container incarnation merely because the five-minute TTL has not expired.
    current_id = getattr(container, "id", None)
    if (not force and current_id is not None
            and PROFILE_CACHE.get("container_id") == current_id
            and PROFILE_CACHE["value"] is not None
            and now - float(PROFILE_CACHE["at"]) < 300):
        return dict(PROFILE_CACHE["value"])
    binary = base._ffmpeg_binary(container)
    config = legacy.read_config()

    # Resolve the VAAPI device from the running container first.
    # Runtime/container state is authoritative because render node numbering
    # can change between hosts (for example renderD129 on Proxmox/LXC).
    # A persisted device is only accepted when it actually exists.
    container_env = base.base._container_env(container)

    env_device = str(container_env.get("VAAPI_DEVICE") or "").strip()
    config_device = str(config.get("transcoding_vaapi_device") or "").strip()

    if env_device and base._exists(container, env_device):
        device = env_device
    elif config_device and base._exists(container, config_device):
        device = config_device
    else:
        device = ""
        for candidate in (
            "/dev/dri/renderD128",
            "/dev/dri/renderD129",
            "/dev/dri/renderD130",
            "/dev/dri/renderD131",
        ):
            if base._exists(container, candidate):
                device = candidate
                break

        if not device:
            device = env_device or config_device or "/dev/dri/renderD128"

    items = [_test_profile(container, profile_id, device, binary) for profile_id in PROFILE_META]
    selected, quality = _selected()
    recommended = _recommended_profile(items)
    hardware = _hardware_detection(items, device)
    backends = _backend_matrix(container, items, device)
    value = {
        "checkedAt": datetime.now(UTC).isoformat(),
        "selected": selected,
        "quality": quality,
        "device": device,
        "ffmpeg": binary,
        "profiles": items,
        "detectedBackend": recommended,
        "hardwareDetection": hardware,
        "backends": backends,
        "rule": (
            "Direct Stream remains Direct Stream. Hardware is detected from real runtime self-tests. "
            "The recommended backend result is diagnostic only; AUTO remains the only execution policy and Stremio remains authoritative for the media decision. "
            "'GPU encode only' leaves decode on CPU; 'Full GPU' is offered only when both H.264 and HEVC hardware decode plus hardware encode pass the runtime test. No silent fallback."
        ),
    }
    PROFILE_CACHE["at"] = now
    PROFILE_CACHE["value"] = dict(value)
    PROFILE_CACHE["container_id"] = current_id
    return value


@app.get("/api/transcoding/profiles")
def profiles():
    return _profiles()


@app.post("/api/transcoding/profiles/refresh")
def refresh_profiles():
    return _profiles(force=True)


@app.put("/api/transcoding/profile")
def set_profile(body: ProfileBody):
    """Compatibility endpoint.

    AUTO is the only accepted mode. Old clients may still call this
    endpoint, but explicit execution profiles are intentionally rejected.
    """
    profile = str(
        body.profile or ""
    ).strip().lower()

    if profile != "auto":
        raise HTTPException(
            status_code=400,
            detail=(
                "Manual transcoding profiles are no longer supported. "
                "The execution mode is Automatic."
            ),
        )

    current = legacy.read_config() or {}

    current["transcoding_profile"] = "auto"
    current["transcoding_resolved_profile"] = ""
    current["transcoding_mode"] = "auto"

    # These values are no longer codec/profile selectors.
    current["transcoding_hwaccel"] = "auto"
    current["transcoding_video_codec"] = "auto"

    if body.quality is not None:
        try:
            quality = int(body.quality)
        except (TypeError, ValueError):
            quality = 22

        current["transcoding_video_quality"] = max(
            0,
            min(51, quality),
        )

    legacy.write_config(current)

    try:
        legacy.audit(
            "transcoding.profile",
            "mode=auto",
        )
    except Exception:
        pass

    PROFILE_CACHE["at"] = 0.0
    PROFILE_CACHE["value"] = None

    return {
        "ok": True,
        "profile": "auto",
        "mode": "auto",
        "resolvedProfile": None,
    }


def _profile_summary(profile: str, quality: int) -> str:
    return (
        "AUTO: hardware acceleration is detected automatically. "
        "Stremio remains authoritative for Direct Stream, transcoding "
        "and codec selection."
    )


def transcoding_status():
    """Expose AUTO-only policy while preserving live runtime telemetry.

    The lower telemetry layers discover processes, capabilities and the actual
    FFmpeg command.  They must not turn persisted legacy profile fields into an
    execution decision: Stremio remains authoritative for copy/transcode and
    codec selection.
    """
    data = base.transcoding_status()
    if not isinstance(data, dict):
        return data

    _, quality = _selected()
    data["executionProfile"] = {"id": "auto", "quality": quality}
    data["policySummary"] = _profile_summary("auto", quality)

    cached_profiles = PROFILE_CACHE.get("value")
    if isinstance(cached_profiles, dict):
        data["verifiedProfiles"] = cached_profiles.get("profiles", [])
        data["profilesCheckedAt"] = cached_profiles.get("checkedAt")
    else:
        data["verifiedProfiles"] = []
        data["profilesCheckedAt"] = None

    policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
    policy.update({
        "transcoding_mode": "auto",
        "transcoding_hwaccel": "auto",
        "transcoding_video_codec": None,
        "transcoding_decode": "core",
    })
    data["policy"] = policy

    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    requested = state.get("requested") if isinstance(state.get("requested"), dict) else {}
    effective = state.get("effective") if isinstance(state.get("effective"), dict) else {}
    requested.update({
        "profile": "auto",
        "mode": "auto",
        "hwaccel": "auto",
        "videoCodec": None,
        "decode": "core",
    })
    effective.update({
        "profile": "auto",
        "mode": "auto",
        "hwaccel": "auto",
        "videoCodec": None,
        "decode": "core",
    })
    state["requested"] = requested
    state["effective"] = effective
    data["state"] = state
    return data


SCRIPT_TAG = '<script src="/transcoding-simple-config.js"></script>'


def profile_home():
    response = base.runtime_home()
    text = response.body.decode("utf-8", errors="replace")
    if SCRIPT_TAG not in text:
        text = text.replace("</body>", f"  {SCRIPT_TAG}\n</body>")
    return HTMLResponse(text, headers={"Cache-Control": "no-store"})


def profile_script():
    return FileResponse(STATIC / "transcoding-simple-config.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})


def _replace_route(path: str) -> None:
    app.router.routes = [route for route in app.router.routes if getattr(route, "path", None) != path]


_replace_route("/api/transcoding/status")
app.add_api_route("/api/transcoding/status", transcoding_status, methods=["GET"])
_replace_route("/")
app.add_api_route("/", profile_home, methods=["GET"])
app.add_api_route("/transcoding-simple-config.js", profile_script, methods=["GET"])
