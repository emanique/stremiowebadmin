from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
WEBADMIN_APP = ROOT / "webadmin" / "app.py"
ENTRYPOINT = ROOT / "docker" / "webadmin_entrypoint.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_config_is_atomic_and_readable(tmp_path, monkeypatch):
    app = _load_module("webadmin_app_config_test", WEBADMIN_APP)
    config = tmp_path / "admin-settings.json"
    monkeypatch.setattr(app, "CONFIG", config)

    persisted = app.write_config(
        {
            "cache_size": 85_899_345_920,
            "seed_on_complete": False,
            "max_streams": 4,
        }
    )

    assert persisted["cache_size"] == 85_899_345_920
    assert persisted["seed_on_complete"] is False
    assert persisted["max_streams"] == 4
    assert json.loads(config.read_text(encoding="utf-8")) == persisted
    assert not list(tmp_path.glob(".*.tmp"))


def test_entrypoint_turns_saved_values_into_stremiosrv_environment(tmp_path, monkeypatch):
    entrypoint = _load_module("webadmin_entrypoint_test", ENTRYPOINT)
    config = tmp_path / "admin-settings.json"
    config.write_text(
        json.dumps(
            {
                "cache_size": 85_899_345_920,
                "seed_on_complete": False,
                "transcode_profile": "vaapi",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("STREMIOSRV_EXTERNAL_CONFIG", str(config))
    monkeypatch.delenv("STREMIOSRV_CACHE_SIZE", raising=False)
    monkeypatch.delenv("STREMIOSRV_SEED_ON_COMPLETE", raising=False)
    monkeypatch.delenv("STREMIOSRV_TRANSCODE_PROFILE", raising=False)

    entrypoint.apply_admin_config()

    assert os.environ["STREMIOSRV_CACHE_SIZE"] == "85899345920"
    assert os.environ["STREMIOSRV_SEED_ON_COMPLETE"] == "false"
    assert os.environ["STREMIOSRV_TRANSCODE_PROFILE"] == "vaapi"


class _ExecResult:
    def __init__(self, exit_code=0, output=b""):
        self.exit_code = exit_code
        self.output = output


class _FakeContainer:
    def __init__(self, config=None):
        self.restarts = 0
        self.config = config or {}
        self.attrs = {"State": {"StartedAt": "before", "Running": True, "Status": "running"}}

    def reload(self):
        started = "after" if self.restarts else "before"
        self.attrs = {"State": {"StartedAt": started, "Running": True, "Status": "running"}}

    def restart(self, timeout=20):
        assert timeout == 20
        self.restarts += 1

    def exec_run(self, argv):
        if argv == ["cat", "/config/admin-settings.json"]:
            return _ExecResult(0, json.dumps(self.config).encode())
        if argv == ["curl", "-fsS", "http://127.0.0.1:11470/health"]:
            return _ExecResult(0, b'{"ok":true}')
        return _ExecResult(1, b"unsupported")


class _FakeContainers:
    def __init__(self, container):
        self.container = container

    def get(self, name):
        assert name == "stremio-libtorrent-server"
        return self.container


class _FakeDocker:
    def __init__(self, container):
        self.containers = _FakeContainers(container)


def test_restart_reloads_saved_configuration_and_waits_for_health(tmp_path, monkeypatch):
    app = _load_module("webadmin_app_restart_test", WEBADMIN_APP)
    config = tmp_path / "admin-settings.json"
    config.write_text('{"cache_size": 85899345920}\n', encoding="utf-8")
    container = _FakeContainer(config={"cache_size": 85899345920})

    monkeypatch.setattr(app, "CONFIG", config)
    monkeypatch.setattr(app, "client", lambda: _FakeDocker(container))
    monkeypatch.setattr(app, "_wait_for_server", lambda container=None, timeout=60.0: (True, ""))
    monkeypatch.setattr(app, "audit", lambda *args, **kwargs: None)

    assert app.RESTART_LOCK.acquire(blocking=False)
    app._restart_worker()

    result = app.restart_status()
    assert result["status"] == "succeeded"
    assert result["startedAtBefore"] == "before"
    assert result["startedAtAfter"] == "after"
    assert container.restarts == 1


def test_server_sees_same_persisted_configuration(monkeypatch):
    app = _load_module("webadmin_app_shared_config_test", WEBADMIN_APP)
    expected = {"cache_size": 85_899_345_920, "max_streams": 3, "seed_on_complete": True}
    container = _FakeContainer(config=expected)
    monkeypatch.setattr(app, "client", lambda: _FakeDocker(container))
    app._verify_server_config(expected)


def test_server_config_mismatch_is_rejected(monkeypatch):
    app = _load_module("webadmin_app_shared_config_mismatch_test", WEBADMIN_APP)
    container = _FakeContainer(config={"max_streams": 1})
    monkeypatch.setattr(app, "client", lambda: _FakeDocker(container))
    with pytest.raises(OSError) as exc:
        app._verify_server_config({"max_streams": 4})
    assert "does not match" in str(exc.value)


def test_wait_for_server_uses_container_local_health():
    app = _load_module("webadmin_app_local_health_test", WEBADMIN_APP)
    container = _FakeContainer()
    ok, detail = app._wait_for_server(container=container, timeout=1)
    assert ok is True
    assert detail == ""


def test_restart_refuses_corrupt_saved_configuration(tmp_path, monkeypatch):
    app = _load_module("webadmin_app_corrupt_config_test", WEBADMIN_APP)
    config = tmp_path / "admin-settings.json"
    config.write_text("{broken", encoding="utf-8")

    monkeypatch.setattr(app, "CONFIG", config)
    monkeypatch.setattr(app, "audit", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as exc:
        app.restart()

    assert exc.value.status_code == 500
    assert "unreadable" in str(exc.value.detail)


def test_restart_verifies_persisted_configuration_after_health(tmp_path, monkeypatch):
    app = _load_module("webadmin_app_restart_verify_test", WEBADMIN_APP)
    config = tmp_path / "admin-settings.json"
    expected = {"cache_size": 85_899_345_920, "max_streams": 3}
    config.write_text(json.dumps(expected), encoding="utf-8")
    container = _FakeContainer(config=expected)

    monkeypatch.setattr(app, "CONFIG", config)
    monkeypatch.setattr(app, "client", lambda: _FakeDocker(container))
    monkeypatch.setattr(app, "_wait_for_server", lambda container=None, timeout=60.0: (True, ""))
    monkeypatch.setattr(app, "audit", lambda *args, **kwargs: None)

    assert app.RESTART_LOCK.acquire(blocking=False)
    app._restart_worker()

    result = app.restart_status()
    assert result["status"] == "succeeded"
    assert result["detail"] == "server restarted and configuration reloaded"
    assert container.restarts == 1



def test_clear_all_logs_resets_file_logs_and_sets_docker_cursors(tmp_path, monkeypatch):
    app = _load_module("webadmin_app_log_clear_test", WEBADMIN_APP)
    state = tmp_path / "state"
    state.mkdir()
    (state / "admin.log").write_text("old admin\n", encoding="utf-8")
    (state / "update-result.json").write_text('{"status":"old"}\n', encoding="utf-8")

    monkeypatch.setattr(app, "STATE", state)
    monkeypatch.setattr(app, "LOG_CURSOR_FILE", state / "log-cursors.json")
    monkeypatch.setattr(app, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(app.time, "time", lambda: 1234567890.0)

    result = app.clear_logs(app.LogBody(source=None))

    assert set(result["cleared"]) == {"application", "admin"}
    assert (state / "admin.log").read_text(encoding="utf-8") == ""
    assert (state / "update-result.json").read_text(encoding="utf-8") == '{"status":"old"}\n'
    cursors = json.loads((state / "log-cursors.json").read_text(encoding="utf-8"))
    assert cursors["application"] == 1234567890.0


def test_webadmin_status_relays_server_playback_activity(monkeypatch):
    app = _load_module("webadmin_app_playback_status_test", WEBADMIN_APP)
    playback = {
        "activeWindowSeconds": 15,
        "sessions": [{"sessionId": "s1", "infoHash": "a" * 40}],
        "active": [{"sessionId": "s1", "infoHash": "a" * 40, "kind": "source"}],
    }

    def fake_get_json(path, fallback):
        if path == "/health":
            return {"status": "healthy", "playbackActivity": playback}
        if path in {"/active.json", "/cache.json", "/pins.json"}:
            return []
        if path == "/stats.json":
            return {}
        return fallback

    monkeypatch.setattr(app, "get_json", fake_get_json)
    monkeypatch.setattr(app, "docker_stats", lambda: (None, 0, 0, 0, 0, 0))

    result = app.status()

    assert result["playback"] == playback



def test_webadmin_status_preserves_unavailable_playback_telemetry(monkeypatch):
    app = _load_module("webadmin_app_playback_unavailable_test", WEBADMIN_APP)

    def fake_get_json(path, fallback):
        if path == "/health":
            return {"status": "healthy"}
        if path in {"/active.json", "/cache.json", "/pins.json"}:
            return []
        if path == "/stats.json":
            return {}
        return fallback

    monkeypatch.setattr(app, "get_json", fake_get_json)
    monkeypatch.setattr(app, "docker_stats", lambda: (None, 0, 0, 0, 0, 0))

    result = app.status()

    assert result["playback"] is None

def test_host_owned_json_fields_cannot_override_compose(monkeypatch, tmp_path):
    import json
    import os
    entrypoint = _load_module("host_owned_entrypoint", ENTRYPOINT)
    config = tmp_path / "admin-settings.json"
    config.write_text(json.dumps({
        "http_port": 19001,
        "bt_listen_port": 9999,
        "cache_root": "/wrong",
        "cert_file": "wrong.pem",
        "enable_upnp": False,
        "server_url": "http://wrong",
        "download_rate_limit": 333,
    }))
    monkeypatch.setenv("STREMIOSRV_EXTERNAL_CONFIG", str(config))
    settings = {
        "STREMIOSRV_HTTP_PORT": "11470",
        "STREMIOSRV_BT_LISTEN_PORT": "47883",
        "STREMIOSRV_CACHE_ROOT": "/root/.stremio-server",
        "STREMIOSRV_CERT_FILE": "certificates.pem",
        "STREMIOSRV_ENABLE_UPNP": "true",
        "STREMIOSRV_SERVER_URL": "https://example.invalid/",
    }
    for key, val in settings.items():
        monkeypatch.setenv(key, val)
    entrypoint.apply_admin_config()
    for key, val in settings.items():
        assert os.environ[key] == val
    assert os.environ["STREMIOSRV_DOWNLOAD_RATE_LIMIT"] == "333"

def test_config_reports_actual_core_bt_port_not_legacy_default(monkeypatch):
    from webadmin import app as module
    monkeypatch.setattr(module, "read_config", lambda: {"library_allow_http": True})
    monkeypatch.setattr(module, "get_json", lambda path, fallback: {
        "listenPort": 47883, "upnpEnabled": False
    })
    monkeypatch.setattr(module, "gateway_port_status", lambda: {"result": "unconfigured"})
    items = {item["name"]: item for item in module.config()["items"]}
    assert items["bt_listen_port"]["value"] == 47883
    assert items["bt_listen_port"]["displayValue"] == 47883
    assert items["bt_listen_port"]["editable"] is False
    assert items["enable_upnp"]["value"] is False
    assert items["library_allow_http"]["value"] is True



def test_gateway_status_distinguishes_local_port_from_verified_lease(monkeypatch):
    from webadmin import app as module
    monkeypatch.setattr(module, "get_json", lambda path, fallback: {"listenPort": 47883})
    monkeypatch.setattr(module, "gateway_port_status", lambda: {
        "result": "unconfigured", "label": "Gateway por configurar", "port": None,
    })
    status = module.gateway_port()
    assert status["effectivePort"] == 47883
    assert status["portSource"] == "host-local"
    assert not status["gatewayVerified"]

    monkeypatch.setattr(module, "gateway_port_status", lambda: {
        "result": "unchanged", "port": 47883, "route": "vpn",
    })
    assigned = module.gateway_port()
    assert assigned["gatewayVerified"]
    assert assigned["portSource"] == "gateway-verified"

    monkeypatch.setattr(module, "gateway_port_status", lambda: {
        "result": "unchanged", "port": 6881, "route": "vpn",
    })
    mismatch = module.gateway_port()
    assert not mismatch["gatewayVerified"]


def test_gateway_status_ui_explains_local_port(monkeypatch):
    from webadmin import app as module
    monkeypatch.setattr(module, "read_config", dict)
    monkeypatch.setattr(module, "get_json", lambda path, fallback: {
        "listenPort": 47883, "upnpEnabled": True,
    })
    monkeypatch.setattr(module, "gateway_port_status", lambda: {
        "result": "unconfigured", "label": "Gateway por configurar",
    })
    result = {v["name"]: v for v in module.config()["items"]}
    msg = result["bt_listen_port"]["description"]
    assert "Porta activa no Core: 47883" in msg
    assert "não comprova uma atribuição NAT-PMP" in msg
    assert result["bt_listen_port"]["editable"] is False


def test_restart_detects_recreated_container_by_stable_name(tmp_path, monkeypatch):
    app = _load_module("webadmin_restart_replaced_test", WEBADMIN_APP)
    class Old(_FakeContainer):
        id = "old-id"
        def restart(self, timeout=20):
            self.restarts += 1
        def reload(self):
            if self.restarts:
                raise RuntimeError("404 No such container: old-id")
            super().reload()
    class New(_FakeContainer):
        id = "new-id"
        def reload(self):
            self.attrs = {"State": {"StartedAt": "new-start", "Running": True}}
    old, new = Old(), New()
    class Containers:
        def get(self, name):
            assert name == app.CONTAINER
            return new if old.restarts else old
    class Docker:
        containers = Containers()
    monkeypatch.setattr(app, "client", lambda: Docker())
    monkeypatch.setattr(app, "CONFIG", tmp_path / "missing.json")
    monkeypatch.setattr(app, "audit", lambda *args, **kw: None)
    assert app.RESTART_LOCK.acquire(blocking=False)
    app._restart_worker()
    assert app.restart_status()["status"] == "succeeded"
    assert app.restart_status()["startedAtAfter"] == "new-start"
    assert new.exec_run(["curl", "-fsS", "http://127.0.0.1:11470/health"]).exit_code == 0


def test_health_poll_re_resolves_replaced_container(monkeypatch):
    app = _load_module("webadmin_restart_health_reresolve", WEBADMIN_APP)
    class Stale(_FakeContainer):
        def reload(self):
            raise RuntimeError("404 No such container: old")
    good = _FakeContainer()
    monkeypatch.setattr(app, "client", lambda: _FakeDocker(good))
    monkeypatch.setattr(app.time, "sleep", lambda _: None)
    ok, detail = app._wait_for_server(container=Stale(), timeout=2)
    assert ok, detail


def test_restart_replacement_during_docker_restart_is_not_false_failure(tmp_path, monkeypatch):
    app = _load_module("webadmin_restart_race_test", WEBADMIN_APP)
    class Old(_FakeContainer):
        id = "old-id"
        def restart(self, timeout=20):
            self.restarts += 1
            raise RuntimeError("404 No such container old-id")
    class New(_FakeContainer):
        id = "new-id"
        def reload(self):
            self.attrs = {"State": {"StartedAt": "new-start", "Running": True}}
    old, new = Old(), New()
    class Containers:
        def get(self, _):
            return new if old.restarts else old
    class Docker:
        containers = Containers()
    monkeypatch.setattr(app, "client", lambda: Docker())
    monkeypatch.setattr(app, "CONFIG", tmp_path / "missing.json")
    monkeypatch.setattr(app, "audit", lambda *args, **kw: None)
    assert app.RESTART_LOCK.acquire(blocking=False)
    app._restart_worker()
    assert app.restart_status()["status"] == "succeeded"
