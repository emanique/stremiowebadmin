#!/usr/bin/env python3
"""Read-only inference backend probe, independent from Stremio transcoding.

Detects real CTranslate2 and OpenVINO devices; does NOT translate CUDA into
SYCL/oneAPI or claim an Intel GPU is ready for an Argos/CTranslate2 model.
There is no host-driver installation, Docker change, or network access.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def openvino_status(core=None) -> dict:
    status = {
        "runtimeInstalled": False,
        "devices": [],
        "intelGpuExposed": False,
        "gpuComputeSmokeTest": False,
        "gpuTranslationModelValidated": False,
        "readyForSubtitleTranslation": False,
        "reason": "",
    }
    try:
        if core is None:
            import openvino as ov
            core = ov.Core()
        devices = list(core.available_devices)
        status["runtimeInstalled"] = True
        for name in devices:
            entry = {"device": name}
            try:
                entry["name"] = str(core.get_property(name, "FULL_DEVICE_NAME"))
            except (RuntimeError, ValueError, KeyError):
                entry["name"] = "unknown"
            status["devices"].append(entry)
        status["intelGpuExposed"] = any(
            item["device"].split(".", 1)[0] == "GPU"
            for item in status["devices"]
        )
        if not status["intelGpuExposed"]:
            status["reason"] = "OpenVINO has no GPU device; VAAPI is not an inference backend"
        else:
            # An enumerated GPU may still fail OpenCL compilation. A deliberately
            # tiny arithmetic graph confirms basic compute, NOT translation.
            try:
                if not hasattr(core, "compile_model"):
                    raise RuntimeError("GPU runtime does not support model compilation")
                import numpy as np
                import openvino as ov
                from openvino import opset13 as ops
                parameter = ops.parameter([1, 4], ov.Type.f32, name="probe")
                graph = ov.Model([ops.add(parameter, ops.constant(
                    np.ones((1, 4), dtype=np.float32)
                ))], [parameter], "subtitle-gpu-smoke-test")
                compiled = core.compile_model(graph, "GPU")
                output = compiled([np.zeros((1, 4), dtype=np.float32)])
                result = next(iter(output.values()))
                status["gpuComputeSmokeTest"] = bool(np.allclose(result, 1.0))
            except (ImportError, AttributeError, OSError, RuntimeError, ValueError) as exc:
                status["reason"] = "GPU enumerated, compute smoke test failed: " + str(exc)[:180]
            if status["gpuComputeSmokeTest"]:
                status["reason"] = (
                    "GPU arithmetic test passed, but a translated and validated "
                    "Argos/OpenVINO NMT model is still required"
                )
            elif not status["reason"]:
                status["reason"] = "GPU compute test failed; cannot select it for translation"
    except ImportError:
        status["reason"] = "OpenVINO Python runtime not installed"
    except (OSError, RuntimeError, ValueError) as exc:
        status["reason"] = f"OpenVINO initialization failed: {str(exc)[:200]}"
    return status


def ctranslate2_status(runtime=None) -> dict:
    status = {
        "runtimeInstalled": False,
        "cudaDevices": 0,
        "cudaComputeTypes": [],
        "cudaBackendAvailable": False,
        "reason": "",
    }
    try:
        if runtime is None:
            import ctranslate2 as runtime
        status["runtimeInstalled"] = True
        count = int(runtime.get_cuda_device_count())
        status["cudaDevices"] = count
        if count:
            types = runtime.get_supported_compute_types("cuda", device_index=0)
            status["cudaComputeTypes"] = sorted(types)
            status["cudaBackendAvailable"] = bool(types)
        status["reason"] = (
            "CUDA runtime discovered; model-specific load still required"
            if status["cudaBackendAvailable"] else "No usable CTranslate2 CUDA backend"
        )
    except ImportError:
        status["reason"] = "CTranslate2 not installed"
    except (OSError, RuntimeError, ValueError) as exc:
        status["reason"] = f"CUDA runtime probe failed: {str(exc)[:200]}"
    return status


def probe(inventory: dict | None = None, *, ov_core=None, ct2_runtime=None) -> dict:
    """Return inference capabilities and independent Stremio assignment context."""
    openvino = openvino_status(ov_core)
    ct2 = ctranslate2_status(ct2_runtime)
    stremio = (inventory or {}).get("stremio", {})
    devices = (inventory or {}).get("gpuInventory", [])
    assigned = set(stremio.get("assignedRenderNodes", []))
    other = [
        {"node": gpu["renderNode"], "vendor": gpu.get("vendor", "unknown"),
         "pci": gpu.get("pci", ""), "assignedToStremio": False,
         "inferenceValidated": False}
        for gpu in devices if gpu.get("renderNode") not in assigned
    ]
    return {
        "schemaVersion": 1,
        "openvino": openvino,
        "ctranslate2": ct2,
        "stremioAssignedRenderNodes": sorted(assigned),
        "otherGpuCandidates": other,
        "policy": {
            "intelGpuTranslationReady": False,
            "reason": (
                "No converted and tested OpenVINO NMT model exists in this "
                "Subtitle Translator build; a GPU plugin alone is insufficient"
            ),
            "automaticFallback": "cpu",
            "videoConfigurationModified": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory", type=Path,
        help="Optional JSON generated by subtitle-gpu-inventory.py on the host",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    inventory = None
    if args.inventory:
        if args.inventory.stat().st_size > 256 * 1024:
            parser.error("inventory is too large")
        data = json.loads(args.inventory.read_text("utf-8"))
        if not isinstance(data, dict):
            parser.error("invalid inventory")
        inventory = data
    result = probe(inventory)
    output = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
