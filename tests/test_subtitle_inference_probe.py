"""No GPU is available to subtitles unless its inference backend is proven."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "subtitle-inference-probe.py"
    spec = importlib.util.spec_from_file_location("subtitle_inference_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeOpenVINO:
    def __init__(self, names):
        self.available_devices = names

    def get_property(self, name, prop):
        return "Intel Graphics" if name.startswith("GPU") else "Intel CPU"


class FakeCT2:
    def __init__(self, cuda=0):
        self.cuda = cuda

    def get_cuda_device_count(self):
        return self.cuda

    def get_supported_compute_types(self, dev, device_index=0):
        return {"float16"} if self.cuda else set()


def test_openvino_cpu_only_no_false_gpu_readiness():
    mod = _module()
    result = mod.openvino_status(FakeOpenVINO(["CPU"]))
    assert result["runtimeInstalled"]
    assert not result["intelGpuExposed"]
    assert not result["readyForSubtitleTranslation"]


def test_openvino_gpu_visible_but_no_translation_model():
    mod = _module()
    result = mod.openvino_status(FakeOpenVINO(["CPU", "GPU.0"]))
    assert result["intelGpuExposed"]
    assert not result["gpuTranslationModelValidated"]
    assert not result["readyForSubtitleTranslation"]


def test_cuda_runtime_probe_preserves_model_specific_gate():
    mod = _module()
    result = mod.ctranslate2_status(FakeCT2(1))
    assert result["cudaBackendAvailable"]
    assert result["cudaDevices"] == 1
    assert "float16" in result["cudaComputeTypes"]


def test_unassigned_nvidia_does_not_make_intel_inference_ready():
    mod = _module()
    inventory = {
        "stremio": {"assignedRenderNodes": ["/dev/dri/renderD129"]},
        "gpuInventory": [
            {"renderNode": "/dev/dri/renderD129", "vendor": "intel", "pci": "0"},
            {"renderNode": "/dev/dri/renderD128", "vendor": "nvidia", "pci": "1"},
        ],
    }
    result = mod.probe(inventory, ov_core=FakeOpenVINO(["CPU"]),
                       ct2_runtime=FakeCT2())
    assert result["stremioAssignedRenderNodes"] == ["/dev/dri/renderD129"]
    assert result["otherGpuCandidates"][0]["vendor"] == "nvidia"
    assert result["otherGpuCandidates"][0]["inferenceValidated"] is False
    assert result["policy"]["intelGpuTranslationReady"] is False
    assert result["policy"]["videoConfigurationModified"] is False
