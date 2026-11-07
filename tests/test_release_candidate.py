"""Release candidate bootstrap checks."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
def load(name,rel):
    spec=importlib.util.spec_from_file_location(name,ROOT/rel)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
def test_gateway_status_regular_idempotent(tmp_path):
    m=load("status_boot","scripts/ensure-port-status.py")
    p=tmp_path/"port-status.json"
    assert m.ensure_status(p)
    assert p.is_file() and "unconfigured" in p.read_text()
    changed=p.stat().st_mtime_ns
    assert not m.ensure_status(p)
    assert p.stat().st_mtime_ns==changed
def test_gateway_status_refuses_directory(tmp_path):
    m=load("status_directory","scripts/ensure-port-status.py")
    p=tmp_path/"port-status.json";p.mkdir()
    with pytest.raises(RuntimeError):m.ensure_status(p)
def test_gateway_status_refuses_symlink(tmp_path):
    m=load("status_symlink","scripts/ensure-port-status.py")
    real=tmp_path/"real";real.write_text("{}")
    p=tmp_path/"port-status.json";p.symlink_to(real)
    with pytest.raises(RuntimeError):m.ensure_status(p)
def test_host_reconcile_no_gateway_mutation(monkeypatch,tmp_path):
    m=load("host_reconcile_test","scripts/reconcile-host.py")
    m.ROOT=tmp_path
    (tmp_path/".env").write_text("IPADDRESS_SOURCE=auto\nIPADDRESS=10.22.0.5\nSTREMIO_DNS_SERVER=10.22.0.1\n")
    calls=[]
    def fake_command(*argv,**kwargs):
        calls.append(argv)
        if argv[0]=="ip":
            return '[{"dev":"eth0","prefsrc":"10.22.0.5","gateway":"10.22.0.1"}]'
        return "DNS unchanged"
    monkeypatch.setattr(m,"command",fake_command)
    class Proc:
        returncode=0
        stdout="unconfigured"
        stderr=""
    monkeypatch.setattr(m.subprocess,"run",lambda *args,**kwargs:Proc())
    assert m.reconcile()==0
    assert not any("up" in argv for argv in calls)
    assert any("boot-gpu-reconcile.py" in " ".join(argv) for argv in calls)
def test_setup_preserves_env():
    script=(ROOT/"packaging/stremio-webadmin-setup.sh").read_text()
    assert 'if [ ! -f "$ROOT/.env" ]' in script
    assert "ensure-port-status.py" in script
    assert "stremio-webadmin-reconcile.timer" in script
def test_release_package_from_committed_sources():
    build=(ROOT/"scripts/build-qa-release.sh").read_text()
    assert "git archive HEAD" in build
    assert "dpkg-deb" in build

def test_existing_deployment_blocks_bt_port_regression(tmp_path):
    m=load("deployment_guard","scripts/validate-existing-deployment.py")
    p=tmp_path/".env";p.write_text("STREMIOSRV_BT_LISTEN_PORT=6881\nIPADDRESS_SOURCE=auto\n")
    info={"Config":{"Env":["STREMIOSRV_BT_LISTEN_PORT=47883","IPADDRESS=192.168.1.199"]}}
    with pytest.raises(RuntimeError,match="Migrate the existing"):
        m.verify_existing(p,info)
    p.write_text("STREMIOSRV_BT_LISTEN_PORT=47883\nIPADDRESS=192.168.1.199\n")
    assert m.verify_existing(p,info)=="existing-matched"
def test_existing_deployment_blocks_ip_mismatch(tmp_path):
    m=load("deployment_guard_ip","scripts/validate-existing-deployment.py")
    p=tmp_path/".env";p.write_text("STREMIOSRV_BT_LISTEN_PORT=47883\nIPADDRESS=192.168.1.221\n")
    info={"Config":{"Env":["STREMIOSRV_BT_LISTEN_PORT=47883","IPADDRESS=192.168.1.199"]}}
    with pytest.raises(RuntimeError,match="Review host network migration"):
        m.verify_existing(p,info)
def test_package_setup_validates_existing_before_launch():
    script=(ROOT/"packaging/stremio-webadmin-setup.sh").read_text()
    assert script.index("validate-existing-deployment.py") < script.index("sh ./start.sh")


@pytest.mark.parametrize("stderr", [
    "error: no such object: stremio-libtorrent-server",
    "Error: No such object: stremio-libtorrent-server",
    "Error: No such container: stremio-libtorrent-server",
])
def test_clean_install_accepts_missing_docker_container(monkeypatch, tmp_path, stderr):
    m=load("deployment_guard_clean","scripts/validate-existing-deployment.py")
    class Missing:
        returncode=1
        stdout="[]"
        def __init__(self, error):self.stderr=error
    monkeypatch.setattr(m.subprocess, "run", lambda *args, **kwargs: Missing(stderr))
    assert m.verify_existing(tmp_path / ".env") == "fresh"


def test_clean_install_refuses_docker_daemon_failure(monkeypatch, tmp_path):
    m=load("deployment_guard_docker_error","scripts/validate-existing-deployment.py")
    class Failure:
        returncode=1
        stdout=""
        stderr="permission denied while trying to connect to the Docker daemon"
    monkeypatch.setattr(m.subprocess,"run",lambda *args, **kwargs: Failure())
    with pytest.raises(RuntimeError, match="Cannot inspect"):
        m.verify_existing(tmp_path / ".env")
