import os

import stremiosrv.transcode.profiler as profiler


def test_vaapi_explicit_device(monkeypatch):
    monkeypatch.setenv(
        "VAAPI_DEVICE",
        "/dev/dri/renderD129",
    )

    monkeypatch.setattr(
        profiler.os.path,
        "exists",
        lambda path: (
            path == "/dev/dri/renderD129"
        ),
    )

    assert (
        profiler.detect_profile()
        == "vaapi-renderD129"
    )

    assert profiler.detect_backend() == {
        "mode": "auto",
        "backend": "vaapi",
        "device": "/dev/dri/renderD129",
    }


def test_render_node_discovery_not_fixed_to_128(
    monkeypatch,
):
    monkeypatch.delenv(
        "VAAPI_DEVICE",
        raising=False,
    )

    monkeypatch.setattr(
        profiler.glob,
        "glob",
        lambda pattern: [
            "/dev/dri/renderD130",
            "/dev/dri/renderD129",
        ],
    )

    monkeypatch.setattr(
        profiler.os.path,
        "exists",
        lambda path: True,
    )

    assert (
        profiler.detect_profile()
        == "vaapi-renderD129"
    )


def test_nvidia_when_no_vaapi(monkeypatch):
    monkeypatch.delenv(
        "VAAPI_DEVICE",
        raising=False,
    )

    monkeypatch.setattr(
        profiler.glob,
        "glob",
        lambda pattern: [],
    )

    monkeypatch.setattr(
        profiler,
        "_nvidia_available",
        lambda: True,
    )

    assert (
        profiler.detect_profile()
        == "nvenc-linux"
    )

    assert profiler.detect_backend() == {
        "mode": "auto",
        "backend": "nvenc",
        "device": None,
    }


def test_no_gpu(monkeypatch):
    monkeypatch.delenv(
        "VAAPI_DEVICE",
        raising=False,
    )

    monkeypatch.setattr(
        profiler.glob,
        "glob",
        lambda pattern: [],
    )

    monkeypatch.setattr(
        profiler,
        "_nvidia_available",
        lambda: False,
    )

    assert profiler.detect_profile() is None

    assert profiler.detect_backend() == {
        "mode": "auto",
        "backend": "none",
        "device": None,
    }


def test_persisted_profile_does_not_control_discovery(
    monkeypatch,
):
    """AUTO hardware discovery has no persisted-profile dependency."""
    monkeypatch.delenv(
        "VAAPI_DEVICE",
        raising=False,
    )

    monkeypatch.setattr(
        profiler.glob,
        "glob",
        lambda pattern: [],
    )

    monkeypatch.setattr(
        profiler,
        "_nvidia_available",
        lambda: False,
    )

    assert not hasattr(
        profiler,
        "CONFIG_FILE",
    )

    assert profiler.detect_profile() is None


def test_vaapi_has_priority_when_exposed(
    monkeypatch,
):
    monkeypatch.setenv(
        "VAAPI_DEVICE",
        "/dev/dri/renderD129",
    )

    monkeypatch.setattr(
        profiler.os.path,
        "exists",
        lambda path: True,
    )

    monkeypatch.setattr(
        profiler,
        "_nvidia_available",
        lambda: True,
    )

    assert (
        profiler.detect_profile()
        == "vaapi-renderD129"
    )
