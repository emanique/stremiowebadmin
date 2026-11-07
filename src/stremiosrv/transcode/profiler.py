"""Automatic hardware backend discovery.

AUTO is the only execution mode.

This module detects which hardware acceleration backend is available.
It does NOT choose the output codec and does NOT override Stremio's
copy/transcode decision.

Possible results:
    vaapi-<render-node>
    nvenc-linux
    None
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess


def _nvidia_available() -> bool:
    if not shutil.which("nvidia-smi"):
        return False

    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return (
        result.returncode == 0
        and b"GPU" in result.stdout
    )


def _vaapi_render_nodes() -> list[str]:
    preferred = str(
        os.environ.get("VAAPI_DEVICE") or ""
    ).strip()

    if preferred and os.path.exists(preferred):
        return [preferred]

    return sorted(
        path
        for path in glob.glob("/dev/dri/renderD*")
        if os.path.exists(path)
    )


def detect_profile() -> str | None:
    """Return detected hardware backend.

    The historical function name is intentionally retained because other
    Stremio server modules already import detect_profile(). Its semantics
    are now backend discovery rather than an encoder policy.
    """

    render_nodes = _vaapi_render_nodes()

    # Prefer the explicitly exposed VAAPI device when one exists.
    if render_nodes:
        return "vaapi-" + os.path.basename(render_nodes[0])

    if _nvidia_available():
        return "nvenc-linux"

    return None


def detect_backend() -> dict[str, str | None]:
    profile = detect_profile()

    if profile and profile.startswith("vaapi-"):
        node = profile.removeprefix("vaapi-")
        return {
            "mode": "auto",
            "backend": "vaapi",
            "device": f"/dev/dri/{node}",
        }

    if profile == "nvenc-linux":
        return {
            "mode": "auto",
            "backend": "nvenc",
            "device": None,
        }

    return {
        "mode": "auto",
        "backend": "none",
        "device": None,
    }
