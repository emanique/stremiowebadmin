"""External inventory must never change the Stremio server's GPU configuration."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "subtitle-gpu-inventory.py"
    spec = importlib.util.spec_from_file_location("subtitle_gpu_inventory", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_device(sysfs: Path, slot: str, name: str, vendor: str, device: str,
                   driver: str) -> None:
    pci = sysfs.parent / slot
    pci.mkdir(parents=True)
    (pci / "vendor").write_text(vendor)
    (pci / "device").write_text(device)
    (pci / "driver").symlink_to(sysfs.parent / "drivers" / driver)
    (sysfs.parent / "drivers" / driver).mkdir(parents=True, exist_ok=True)
    (sysfs / name).mkdir()
    (sysfs / name / "device").symlink_to(pci, target_is_directory=True)


def test_gpu_inventory_identifies_distinct_vendors_by_drm_node(tmp_path):
    inventory = _module()
    drm = tmp_path / "drm"
    drm.mkdir()
    _render_device(drm, "0000:00:02.0", "renderD129", "0x8086", "0x1916", "i915")
    _render_device(drm, "0000:01:00.0", "renderD128", "0x10de", "0x1299", "nvidia")
    found = inventory.drm_devices(drm)
    assert len(found) == 2
    mapping = {x["renderNode"]: x["vendor"] for x in found}
    assert mapping == {"/dev/dri/renderD128": "nvidia", "/dev/dri/renderD129": "intel"}
    assert all(x["capabilities"]["inference"] == "not verified by DRM enumeration"
               for x in found)


def test_external_inventory_lists_unassigned_candidate_not_gpu_readiness(monkeypatch):
    inventory = _module()
    monkeypatch.setattr(inventory, "drm_devices", lambda _: [
        {"renderNode": "/dev/dri/renderD128", "vendor": "nvidia",
         "pci": "0000:01:00.0"},
        {"renderNode": "/dev/dri/renderD129", "vendor": "intel",
         "pci": "0000:00:02.0"},
    ])
    monkeypatch.setattr(inventory, "_stremio_device_use", lambda _: {
        "assignedRenderNodes": ["/dev/dri/renderD129"],
        "transcodingSettings": {"VAAPI_DEVICE": "/dev/dri/renderD129"},
    })
    monkeypatch.setattr(inventory, "_nvidia", lambda: {"detected": []})
    result = inventory.inventory()
    assert len(result["unassignedGpuCandidates"]) == 1
    assert result["unassignedGpuCandidates"][0]["vendor"] == "nvidia"
    assert result["unassignedGpuCandidates"][0]["inferenceReady"] is False


def test_inventory_without_docker_does_not_assume_gpu_inference(monkeypatch, tmp_path):
    inventory = _module()
    monkeypatch.setattr(inventory, "_run", lambda *args, **kwargs: "")
    result = inventory._stremio_device_use("missing-container")
    assert result["assignedRenderNodes"] == []
    assert result["transcodingSettings"] == {}


def test_nvidia_visible_to_stremio_is_not_claimed_as_exclusive(monkeypatch):
    inventory = _module()
    monkeypatch.setattr(inventory, "drm_devices", lambda _: [
        {"renderNode": "/dev/dri/renderD128", "vendor": "nvidia",
         "pci": "0000:01:00.0"},
        {"renderNode": "/dev/dri/renderD129", "vendor": "intel",
         "pci": "0000:00:02.0"},
    ])
    monkeypatch.setattr(inventory, "_stremio_device_use", lambda _: {
        "assignedRenderNodes": ["/dev/dri/renderD129"],
        "nvidiaVisibility": "all",
        "gpuDeviceRequests": [],
        "transcodingSettings": {"VAAPI_DEVICE": "/dev/dri/renderD129",
                                "NVIDIA_VISIBLE_DEVICES": "all"},
    })
    monkeypatch.setattr(inventory, "_nvidia", lambda: {"detected": []})
    result = inventory.inventory()
    assert result["policy"]["nvidiaPotentiallyShared"]
    other = result["unassignedGpuCandidates"][0]
    assert other["vendor"] == "nvidia"
    assert other["potentiallySharedWithStremio"]
    assert not other["inferenceReady"]
