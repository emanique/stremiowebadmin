"""Inference acceleration policy independent from Stremio's VAAPI video pipeline.

VAAPI render nodes expose video acceleration, *not* CUDA kernels.
A GPU is eligible only after CTranslate2 itself proves it can use CUDA.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from dataclasses import dataclass


@dataclass(frozen=True)
class BackendStatus:
    requested: str
    selected: str
    reason: str
    cuda_devices: int
    supported_compute_types: tuple[str, ...]

    def public(self) -> dict:
        return {
            "requested": self.requested,
            "selected": self.selected,
            "reason": self.reason,
            "cudaDevices": self.cuda_devices,
            "computeTypes": list(self.supported_compute_types),
            "vaapiShared": False,
        }


class BackendUnavailable(RuntimeError):
    pass


def configured_device() -> str:
    """WebAdmin override for inference only, live from persistent settings.

    Falls back to the container environment. This never alters VAAPI_DEVICE,
    NVIDIA_VISIBLE_DEVICES or transcoding hardware acceleration settings.
    """
    fallback = os.getenv("STREMIOSRV_SUBTITLE_DEVICE", "auto")
    config = Path(os.getenv(
        "STREMIOSRV_EXTERNAL_CONFIG", "/config/admin-settings.json"
    ))
    try:
        if config.stat().st_size > 128 * 1024:
            return fallback
        data = json.loads(config.read_text("utf-8"))
        if isinstance(data, dict):
            selected = data.get("subtitle_device", fallback)
            if isinstance(selected, str):
                return selected
    except (OSError, ValueError):
        pass
    return fallback


def backend_policy(value: str | None = None, ct2=None) -> BackendStatus:
    """Select CPU or CUDA using real CTranslate2 runtime evidence.

    auto: CUDA only when a usable device and compute backend exist, else CPU.
    cpu: always CPU, even when video transcoding uses NVENC/VAAPI.
    cuda: fail closed when unavailable; never silently run on CPU.
    """
    requested = (value if value is not None else
                 configured_device()).strip().lower()
    if requested not in {"auto", "cpu", "cuda"}:
        raise ValueError("subtitle device must be auto, cpu, or cuda")
    if ct2 is None:
        try:
            import ctranslate2 as ct2
        except ImportError as exc:
            raise BackendUnavailable("CTranslate2 runtime not installed") from exc
    if requested == "cpu":
        return BackendStatus(requested, "cpu", "CPU policy explicitly selected", 0, ())
    count = 0
    supported: tuple[str, ...] = ()
    reason = "No usable CUDA device for CTranslate2"
    try:
        count = int(ct2.get_cuda_device_count())
        if count > 0:
            supported = tuple(sorted(ct2.get_supported_compute_types("cuda", device_index=0)))
            if supported:
                return BackendStatus(requested, "cuda", "CTranslate2 CUDA backend available",
                                     count, supported)
    except (RuntimeError, OSError, ValueError) as exc:
        reason = "CUDA runtime or driver unavailable: " + str(exc)[:200]
    if requested == "cuda":
        raise BackendUnavailable(reason)
    return BackendStatus(requested, "cpu", reason, count, supported)


def runtime_status() -> dict:
    """Status endpoint helper: no video/VAAPI device reconfiguration."""
    try:
        return backend_policy().public()
    except (BackendUnavailable, ValueError) as exc:
        return {"requested": os.getenv("STREMIOSRV_SUBTITLE_DEVICE", "auto"),
                "selected": "unavailable", "reason": str(exc),
                "cudaDevices": 0, "computeTypes": [], "vaapiShared": False}
