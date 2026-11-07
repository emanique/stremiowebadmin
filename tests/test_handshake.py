from fastapi.testclient import TestClient

from stremiosrv.app import create_app


def test_settings_shape():
    c = TestClient(create_app())
    b = c.get("/settings").json()
    assert set(b) >= {"options", "values", "baseUrl"}
    assert "btMaxConnections" in b["values"]
    assert b["values"]["cacheRoot"].endswith(".stremio-server")
    # Must look like a real Stremio server version or native clients (desktop v6) reject us and
    # fall back to their bundled 127.0.0.1 server.
    assert b["values"]["serverVersion"] == "4.21.1"


def test_base_url_reflects_request():
    c = TestClient(create_app())
    b = c.get("/settings", headers={"x-forwarded-proto": "https", "host": "example.com:12470"}).json()
    assert b["baseUrl"] == "https://example.com:12470"


def test_network_and_device_info():
    c = TestClient(create_app())
    assert "availableInterfaces" in c.get("/network-info").json()
    assert "availableHardwareAccelerations" in c.get("/device-info").json()


def test_global_stats_shape():
    c = TestClient(create_app())
    b = c.get("/stats.json").json()
    assert set(b) >= {"cache", "playback", "unmatchedRoutes", "proxyRefused"}
    assert set(b["proxyRefused"]) == {"home", "internet"}
    assert set(b["cache"]) >= {"cacheUsed", "cacheSize", "diskFree", "diskTotal"}
    assert set(b["playback"]) >= {"stalls", "stallSeconds", "timeouts"}


def test_transcode_stats_no_gpu(monkeypatch):
    """AUTO reports no accelerator when hardware discovery finds none."""
    from stremiosrv.config import Settings
    import stremiosrv.transcode.profiler as profiler

    monkeypatch.setattr(
        profiler,
        "detect_backend",
        lambda: {
            "mode": "auto",
            "backend": "none",
            "device": None,
        },
    )

    class FakeConv:
        def active_count(self):
            return 0

    c = TestClient(
        create_app(
            settings=Settings(),
            converter=FakeConv(),
        )
    )

    payload = c.get("/transcode.json").json()

    assert payload == {
        "hwAccel": False,
        "profile": "auto",
        "backend": "none",
        "device": None,
        "videoCodec": None,
        "hwDecode": False,
        "activeTranscodes": 0,
    }


def test_transcode_stats_with_nvidia_and_jobs(monkeypatch):
    """AUTO exposes NVIDIA capability without selecting a codec."""
    from stremiosrv.config import Settings
    import stremiosrv.transcode.profiler as profiler

    monkeypatch.setattr(
        profiler,
        "detect_backend",
        lambda: {
            "mode": "auto",
            "backend": "nvenc",
            "device": None,
        },
    )

    class FakeConv:
        def active_count(self):
            return 2

    settings = Settings()

    # Legacy value must not become the execution policy.
    settings.transcode_profile = "nvenc-linux"

    c = TestClient(
        create_app(
            settings=settings,
            converter=FakeConv(),
        )
    )

    payload = c.get("/transcode.json").json()

    assert payload == {
        "hwAccel": True,
        "profile": "auto",
        "backend": "nvenc",
        "device": None,
        "videoCodec": None,
        "hwDecode": False,
        "activeTranscodes": 2,
    }


def test_transcode_stats_with_vaapi(monkeypatch):
    """AUTO exposes the detected VAAPI render node."""
    from stremiosrv.config import Settings
    import stremiosrv.transcode.profiler as profiler

    monkeypatch.setattr(
        profiler,
        "detect_backend",
        lambda: {
            "mode": "auto",
            "backend": "vaapi",
            "device": "/dev/dri/renderD129",
        },
    )

    settings = Settings()

    # Deliberately conflicting legacy profile.
    settings.transcode_profile = "nvenc-linux"

    c = TestClient(
        create_app(settings=settings)
    )

    payload = c.get("/transcode.json").json()

    assert payload["profile"] == "auto"
    assert payload["hwAccel"] is True
    assert payload["backend"] == "vaapi"
    assert payload["device"] == "/dev/dri/renderD129"
    assert payload["videoCodec"] is None
    assert payload["hwDecode"] is False


def test_transcode_legacy_profile_does_not_override_auto(monkeypatch):
    """A persisted legacy profile cannot override AUTO discovery."""
    from stremiosrv.config import Settings
    import stremiosrv.transcode.profiler as profiler

    monkeypatch.setattr(
        profiler,
        "detect_backend",
        lambda: {
            "mode": "auto",
            "backend": "none",
            "device": None,
        },
    )

    settings = Settings()
    settings.transcode_profile = "vaapi-full-h264"

    c = TestClient(
        create_app(settings=settings)
    )

    payload = c.get("/transcode.json").json()

    assert payload["profile"] == "auto"
    assert payload["backend"] == "none"
    assert payload["hwAccel"] is False


def test_transcode_auto_never_reports_forced_codec(monkeypatch):
    """AUTO hardware capability must not become a codec policy."""
    from stremiosrv.config import Settings
    import stremiosrv.transcode.profiler as profiler

    monkeypatch.setattr(
        profiler,
        "detect_backend",
        lambda: {
            "mode": "auto",
            "backend": "vaapi",
            "device": "/dev/dri/renderD129",
        },
    )

    c = TestClient(
        create_app(settings=Settings())
    )

    payload = c.get("/transcode.json").json()

    assert payload["profile"] == "auto"
    assert payload["backend"] == "vaapi"
    assert payload["videoCodec"] is None
