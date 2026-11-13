"""GPU inference decisions must never confuse VAAPI and CUDA support."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from stremiosrv.subtitle_translator.hardware import (
    BackendUnavailable, backend_policy,
)
from stremiosrv.subtitle_translator import local_engine


class FakeRuntime:
    def __init__(self, count=0, types=(), error=None):
        self.count = count
        self.types = types
        self.error = error

    def get_cuda_device_count(self):
        if self.error:
            raise RuntimeError(self.error)
        return self.count

    def get_supported_compute_types(self, device, device_index=0):
        assert device == "cuda"
        assert device_index == 0
        return set(self.types)


def test_auto_uses_cpu_for_vaapi_only_machine(monkeypatch):
    monkeypatch.setenv("VAAPI_DEVICE", "/dev/dri/renderD128")
    assert backend_policy("auto", FakeRuntime()).selected == "cpu"


def test_auto_prefers_functional_cuda_runtime():
    status = backend_policy("auto", FakeRuntime(1, {"float16", "int8"}))
    assert status.selected == "cuda"
    assert status.cuda_devices == 1
    assert "float16" in status.supported_compute_types


def test_forced_cpu_ignores_cuda_gpu():
    status = backend_policy("cpu", FakeRuntime(2, {"float16"}))
    assert status.selected == "cpu"


def test_forced_cuda_fails_instead_of_silent_cpu():
    with pytest.raises(BackendUnavailable):
        backend_policy("cuda", FakeRuntime(error="driver mismatch"))


def test_invalid_backend_setting_rejected():
    with pytest.raises(ValueError):
        backend_policy("vaapi", FakeRuntime())


def test_auto_fallback_on_cuda_model_load_error(monkeypatch):
    calls = []
    monkeypatch.setattr(local_engine, "backend_policy", lambda: SimpleNamespace(
        requested="auto", selected="cuda"))
    def load(src, dst, loc, device="cpu"):
        calls.append(device)
        if device == "cuda":
            raise RuntimeError("not enough GPU memory")
        return ("tokenizer", "translator")
    monkeypatch.setattr(local_engine, "_load_pair", load)
    assert local_engine._select_model("es", "pt", "/models") == ("tokenizer", "translator")
    assert calls == ["cuda", "cpu"]


def test_forced_cuda_model_load_does_not_fallback(monkeypatch):
    monkeypatch.setattr(local_engine, "backend_policy", lambda: SimpleNamespace(
        requested="cuda", selected="cuda"))
    calls = []
    def load(src, dst, loc, device="cpu"):
        calls.append(device)
        raise RuntimeError("model load failed")
    monkeypatch.setattr(local_engine, "_load_pair", load)
    with pytest.raises(RuntimeError):
        local_engine._select_model("es", "pt", "/models")
    assert calls == ["cuda"]


def test_webadmin_gpu_policy_is_live_without_transcode_changes(tmp_path, monkeypatch):
    from stremiosrv.subtitle_translator.hardware import configured_device
    p = tmp_path / "admin-settings.json"
    monkeypatch.setenv("STREMIOSRV_EXTERNAL_CONFIG", str(p))
    monkeypatch.setenv("STREMIOSRV_SUBTITLE_DEVICE", "auto")
    monkeypatch.setenv("VAAPI_DEVICE", "/dev/dri/renderD128")
    p.write_text('{"subtitle_device":"cpu"}')
    assert configured_device() == "cpu"
    p.write_text('{"subtitle_device":"cuda"}')
    assert configured_device() == "cuda"
    assert backend_policy(None, FakeRuntime(1, {"float16"})).selected == "cuda"
    assert __import__("os").environ["VAAPI_DEVICE"] == "/dev/dri/renderD128"


def test_absent_gpu_config_falls_back_to_environment(tmp_path, monkeypatch):
    from stremiosrv.subtitle_translator.hardware import configured_device
    monkeypatch.setenv("STREMIOSRV_EXTERNAL_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setenv("STREMIOSRV_SUBTITLE_DEVICE", "cpu")
    assert configured_device() == "cpu"


def test_forced_cuda_is_unavailable_with_cpu_only_runtime():
    with pytest.raises(BackendUnavailable):
        backend_policy("cuda", FakeRuntime(0))


def test_auto_recovers_from_cuda_failure_mid_inference(monkeypatch):
    monkeypatch.setattr(local_engine, "installed_pairs", lambda root=None: {("es", "pt")})
    class SP:
        def encode(self, text, out_type=str):
            return [text]
        def decode(self, pieces):
            return "pt:" + pieces[0]
    class GPU:
        device = "cuda"
        def translate_batch(self, items, **kwargs):
            raise RuntimeError("CUDA out of memory")
    class CPU:
        device = "cpu"
        def translate_batch(self, items, **kwargs):
            return [SimpleNamespace(hypotheses=[tokens]) for tokens in items]
    cpu = CPU()
    monkeypatch.setattr(local_engine, "backend_policy",
                        lambda: SimpleNamespace(requested="auto", selected="cuda"))
    monkeypatch.setattr(local_engine, "_select_model",
                        lambda *args: (SP(), GPU()))
    monkeypatch.setattr(local_engine, "_load_pair",
                        lambda *args: (SP(), cpu))
    result = local_engine.translate_many(["hola", "adios"], "es", batch_size=1)
    assert result == ["pt:hola", "pt:adios"]


def test_forced_cuda_inference_failure_does_not_fallback(monkeypatch):
    monkeypatch.setattr(local_engine, "installed_pairs", lambda root=None: {("es", "pt")})
    class SP:
        def encode(self, text, out_type=str):
            return [text]
    class GPU:
        device = "cuda"
        def translate_batch(self, items, **kwargs):
            raise RuntimeError("GPU lost")
    monkeypatch.setattr(local_engine, "backend_policy",
                        lambda: SimpleNamespace(requested="cuda", selected="cuda"))
    monkeypatch.setattr(local_engine, "_select_model", lambda *args: (SP(), GPU()))
    with pytest.raises(RuntimeError, match="GPU lost"):
        local_engine.translate_many(["hola"], "es")
