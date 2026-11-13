"""Deployment identity and GPU settings are host-specific."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import stat

ROOT=Path(__file__).resolve().parents[1]


def load(name, relative):
    spec=importlib.util.spec_from_file_location(name, ROOT/relative)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_host_ip_atomic_update_keeps_other_settings(tmp_path):
    mod=load("host_env_test","scripts/sync-host-env.py")
    env=tmp_path/".env"
    env.write_text("IPADDRESS_SOURCE=auto\nIPADDRESS=10.10.10.12\nSTREMIOSRV_BT_LISTEN_PORT=47883\nGPU_BACKEND=auto\n")
    env.chmod(0o600)
    assert mod.persist(env,"10.10.10.13") is True
    updated=env.read_text()
    assert "IPADDRESS=10.10.10.13" in updated
    assert "STREMIOSRV_BT_LISTEN_PORT=47883" in updated
    assert "GPU_BACKEND=auto" in updated
    assert stat.S_IMODE(env.stat().st_mode)==0o600
    previous=env.stat().st_mtime_ns
    assert mod.persist(env,"10.10.10.13") is False
    assert env.stat().st_mtime_ns==previous


def test_duplicate_host_ip_fails_closed(tmp_path):
    import pytest
    mod=load("host_env_dup","scripts/sync-host-env.py")
    env=tmp_path/".env"
    env.write_text("IPADDRESS=10.0.0.1\nIPADDRESS=10.0.0.2\n")
    with pytest.raises(ValueError,match="duplicate"):
        mod.persist(env,"10.0.0.3")


def test_https_config_generated_per_host():
    mod=load("https_host","scripts/configure-host-https.py")
    for ip in ("10.0.40.15","172.16.5.8","192.168.77.9"):
        # Passwordless access is allowed only with explicit trusted LAN CIDR;
        # the standalone Nginx template defaults to Basic Auth otherwise.
        import ipaddress
        trusted=str(ipaddress.ip_network(ip + '/24',strict=False))
        site=mod.nginx_site(ip,auth_mode='lan',trusted_cidr=trusted)
        assert f"listen {ip}:443 ssl;" in site
        assert f"server_name {ip};" in site
        assert "proxy_pass http://127.0.0.1:8090;" in site
        assert "192.168.1.221" not in site
        assert "auth_basic " not in site
        assert f"allow {trusted};" in site
        assert "deny all;" in site
        assert "auth_basic " in mod.nginx_site(ip,auth_mode='basic')


def test_gateway_status_not_hardcoded():
    from webadmin.app import _valid_gateway_address
    assert _valid_gateway_address("192.168.1.231")=="192.168.1.231"
    assert _valid_gateway_address("10.3.10.1")=="10.3.10.1"
    assert _valid_gateway_address("8.8.8.8") is None
    assert _valid_gateway_address("not-ip") is None
