#!/usr/bin/env python3
"""Independent, read-only Linux GPU inventory for Subtitle Translator.

Never changes VAAPI, CUDA, Docker configuration, kernel drivers or Stremio.
Execute on the HOST, not inside the Core. JSON output is an inventory, not a
guarantee that a GPU can run CTranslate2 or OpenVINO inference.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

VENDORS = {"0x8086": "intel", "0x10de": "nvidia", "0x1002": "amd"}


def _read(path: Path) -> str:
    try:
        return path.read_text("utf-8").strip()
    except OSError:
        return ""


def _run(args: list[str], timeout: int = 4) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                timeout=timeout, check=False)
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _pci_device(path: Path) -> dict:
    resolved = path.resolve()
    vendor = _read(resolved / "vendor")
    driver_link = resolved / "driver"
    driver = driver_link.resolve().name if driver_link.exists() else ""
    return {
        "pci": resolved.name,
        "vendor": VENDORS.get(vendor, vendor or "unknown"),
        "vendorId": vendor,
        "deviceId": _read(resolved / "device"),
        "driver": driver,
    }


def drm_devices(sysfs: Path = Path("/sys/class/drm")) -> list[dict]:
    devices = []
    for render in sorted(sysfs.glob("renderD[0-9]*")):
        try:
            base = render / "device"
            if not base.exists():
                continue
            record = _pci_device(base)
            record["renderNode"] = "/dev/dri/" + render.name
            record["capabilities"] = {
                "vaapi": "unknown until vainfo validates codec support",
                "inference": "not verified by DRM enumeration",
            }
            devices.append(record)
        except (OSError, ValueError):
            continue
    return devices


def _stremio_device_use(container: str) -> dict:
    """Docker settings indicate assignment; they do not prove active GPU load."""
    output = _run(["docker", "inspect", container,
                   "--format", "{{json .HostConfig.Devices}}"], timeout=3)
    environment = _run(["docker", "inspect", container,
                        "--format", "{{range .Config.Env}}{{println .}}{{end}}"], timeout=3)
    device_requests = _run(["docker", "inspect", container,
                            "--format", "{{json .HostConfig.DeviceRequests}}"], timeout=3)
    try:
        entries = json.loads(output) if output.strip() else []
    except ValueError:
        entries = []
    devices = [
        item.get("PathOnHost", "") for item in entries
        if isinstance(item, dict) and item.get("PathOnHost")
    ]
    env = {}
    for line in environment.splitlines():
        name, sep, value = line.partition("=")
        if sep and name in ("VAAPI_DEVICE", "TRANSCODING_HWACCEL",
                            "NVIDIA_VISIBLE_DEVICES", "TRANSCODING_MODE"):
            env[name] = value
    try:
        gpu_requests = json.loads(device_requests) if device_requests.strip() else []
    except ValueError:
        gpu_requests = []
    return {
        "container": container,
        "assignedRenderNodes": devices,
        "nvidiaVisibility": env.get("NVIDIA_VISIBLE_DEVICES", ""),
        "gpuDeviceRequests": gpu_requests if isinstance(gpu_requests, list) else [],
        "transcodingSettings": env,
        "note": "assignment only; process utilisation needs separate measurement",
    }


def _nvidia() -> dict:
    info = _run(["nvidia-smi", "--query-gpu=index,name,driver_version",
                 "--format=csv,noheader,nounits"])
    result = []
    for line in info.splitlines():
        parts = [x.strip() for x in line.split(",", 2)]
        if len(parts) == 3:
            result.append({"index": parts[0], "name": parts[1], "driver": parts[2]})
    return {
        "detected": result,
        "usableForCTranslate2": "requires separate runtime CUDA probe",
        "activeComputeProcesses": _run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name",
             "--format=csv,noheader"], timeout=3
        ).strip().splitlines() or [],
    }


def inventory(container: str = "stremio-libtorrent-server",
              sysfs: Path = Path("/sys/class/drm")) -> dict:
    cards = drm_devices(sysfs)
    usage = _stremio_device_use(container)
    assigned = set(usage["assignedRenderNodes"])
    # NVIDIA_VISIBLE_DEVICES=all and Docker GPU requests can expose a
    # NVIDIA compute card even when no DRM node was directly bind-mounted.
    # Treat it as potentially shared, not as a reserved/unused card.
    nvidia_visibility = str(usage.get("nvidiaVisibility", "")).lower()
    requested = usage.get("gpuDeviceRequests", [])
    shares_nvidia = (
        nvidia_visibility not in ("", "none", "void") or
        bool(requested)
    )
    # A render node assigned for transcoding should not be selected
    # *by policy* for subtitle inference; another GPU is only a candidate.
    other = [
        {"vendor": gpu["vendor"], "renderNode": gpu["renderNode"],
         "pci": gpu["pci"], "inferenceReady": False,
         "potentiallySharedWithStremio": (
             gpu["vendor"] == "nvidia" and shares_nvidia
         )}
        for gpu in cards if gpu["renderNode"] not in assigned
    ]
    return {
        "schemaVersion": 1,
        "gpuInventory": cards,
        "stremio": usage,
        "unassignedGpuCandidates": other,
        "nvidia": _nvidia(),
        "policy": {
            "isolation": "GPU assignment read only; no Stremio changes",
            "nvidiaPotentiallyShared": shares_nvidia,
            "intelBackend": "Intel GPU requires separately verified OpenVINO/oneAPI model",
            "nvidiaBackend": "CUDA requires verified driver/CTranslate2 compatibility",
            "fallback": "cpu",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="stremio-libtorrent-server")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    result = inventory(args.container)
    serialized = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Write-only report, not live settings; owner controls output path.
        temp = args.output.with_suffix(args.output.suffix + ".tmp")
        temp.write_text(serialized, encoding="utf-8")
        os.replace(temp, args.output)
    else:
        sys.stdout.write(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
