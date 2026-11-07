"""Fail-closed reconciliation and reversible port transitions.

Simulates Docker and .env without connecting to a VPN provider or restarting
production containers. This is not a replacement for end-to-end lease QA.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gateway_port_reconcile_qa", ROOT / "scripts/gateway-port-sync.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def harness(monkeypatch, tmp_path, *, initial=40819, healthy=True, fail_recreate=False):
    env = tmp_path / ".env"
    env.write_text(f"OTHER=value\nSTREMIOSRV_BT_LISTEN_PORT={initial}\n")
    monkeypatch.setattr(mod, "ENV", env)
    state = {"port": initial, "commands": [], "gpu_checks": 0, "healthy": healthy}

    def effective_port():
        return state["port"]

    def run(command, **kwargs):
        state["commands"].append(command)
        if command[:2] == ["sh", "start.sh"] and "--force-recreate" in command:
            target = int(next(line.partition("=")[2] for line in env.read_text().splitlines()
                              if line.startswith(mod.KEY + "=")))
            if fail_recreate and target != initial:
                raise RuntimeError("simulated new-container recreate failure")
            state["port"] = target
        if command[:3] == ["docker", "inspect", "-f"]:
            return SimpleNamespace(stdout="healthy" if state["healthy"] else "unhealthy")
        return SimpleNamespace(stdout="")

    def verify_gpu():
        state["gpu_checks"] += 1
        return True

    monkeypatch.setattr(mod, "effective_port", effective_port)
    monkeypatch.setattr(mod, "run", run)
    monkeypatch.setattr(mod, "verify_gpu_contract", verify_gpu)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    return env, state


def test_successful_lease_change_recreates_only_stremio(monkeypatch, tmp_path):
    env, state = harness(monkeypatch, tmp_path)
    assert mod.reconcile(45001) == "recreated"
    assert "STREMIOSRV_BT_LISTEN_PORT=45001" in env.read_text()
    assert state["port"] == 45001
    recreate = [c for c in state["commands"] if "--force-recreate" in c]
    assert len(recreate) == 1
    assert recreate[0][-1] == "stremio-libtorrent-server"
    assert state["gpu_checks"] >= 1


def test_no_change_requires_env_and_docker_agreement(monkeypatch, tmp_path):
    env, state = harness(monkeypatch, tmp_path)
    assert mod.reconcile(40819) == "unchanged"
    assert not state["commands"]
    env.write_text("STREMIOSRV_BT_LISTEN_PORT=45001\n")
    with pytest.raises(mod.PortSyncError, match="disagree"):
        mod.reconcile(40819)
    assert not state["commands"]


def test_failed_recreate_rolls_back_original_port(monkeypatch, tmp_path):
    env, state = harness(monkeypatch, tmp_path, fail_recreate=True)
    with pytest.raises(RuntimeError, match="simulated"):
        mod.reconcile(45001)
    assert "STREMIOSRV_BT_LISTEN_PORT=40819" in env.read_text()
    assert state["port"] == 40819
    recreates = [c for c in state["commands"] if "--force-recreate" in c]
    assert len(recreates) == 2
    assert all(c[-1] == "stremio-libtorrent-server" for c in recreates)


@pytest.mark.parametrize("contents", [
    "OTHER=1\n",
    "STREMIOSRV_BT_LISTEN_PORT=wrong\n",
    "STREMIOSRV_BT_LISTEN_PORT=40819\nSTREMIOSRV_BT_LISTEN_PORT=40819\n",
])
def test_corrupt_env_fails_closed(monkeypatch, tmp_path, contents):
    env, state = harness(monkeypatch, tmp_path)
    env.write_text(contents)
    with pytest.raises(mod.PortSyncError):
        mod.reconcile(40819)
    assert not state["commands"]


def test_unhealthy_new_container_restores_old_configuration(monkeypatch, tmp_path):
    env, state = harness(monkeypatch, tmp_path)
    state["healthy"] = True
    inspect_count = 0

    original_run = mod.run
    def delayed_qa(cmd, **kwargs):
        nonlocal inspect_count
        if cmd[:3] == ["docker", "inspect", "-f"]:
            inspect_count += 1
            return SimpleNamespace(stdout="unhealthy" if inspect_count <= 12 else "healthy")
        return original_run(cmd, **kwargs)
    monkeypatch.setattr(mod, "run", delayed_qa)
    with pytest.raises(mod.PortSyncError, match="new port did not become healthy"):
        mod.reconcile(45001)
    assert "STREMIOSRV_BT_LISTEN_PORT=40819" in env.read_text()
    assert state["port"] == 40819
