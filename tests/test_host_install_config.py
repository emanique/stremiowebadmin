"""Deployment identity and GPU settings are host-specific."""
from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

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
        site=mod.nginx_site(ip)
        assert f"listen {ip}:443 ssl;" in site
        assert f"server_name {ip};" in site
        assert "proxy_pass http://127.0.0.1:8090;" in site
        assert "192.168.1.221" not in site
        assert 'auth_basic "Stremio WebAdmin";' in site
        assert "auth_basic_user_file /etc/stremio-webadmin/webadmin.htpasswd;" in site


def test_gateway_status_not_hardcoded():
    from webadmin.app import _valid_gateway_address
    assert _valid_gateway_address("192.168.1.231")=="192.168.1.231"
    assert _valid_gateway_address("10.3.10.1")=="10.3.10.1"
    assert _valid_gateway_address("8.8.8.8") is None
    assert _valid_gateway_address("not-ip") is None


def test_explicit_lan_mode_disables_password_only_in_trusted_network(tmp_path):
    import pytest
    mod=load("https_host_lan","scripts/configure-host-https.py")
    policy=tmp_path/"access-policy"
    from types import SimpleNamespace
    monkey_ip="192.168.1.234"
    original=mod.execute
    def fake_execute(*args, **kwargs):
        if args[:4] == ("ip", "-4", "-j", "address"):
            return SimpleNamespace(stdout='[{"addr_info":[{"family":"inet","local":"192.168.1.234","prefixlen":24}]}]')
        return original(*args, **kwargs)
    mod.execute=fake_execute
    assert mod.read_access_policy(tmp_path, monkey_ip)==("lan","192.168.1.0/24")
    mod.execute=original
    policy.write_text("lan:192.168.1.0/24\\n".replace("\\n","\n"))
    mode,subnet=mod.read_access_policy(tmp_path)
    assert (mode,subnet)==("lan","192.168.1.0/24")
    site=mod.nginx_site("192.168.1.234", mode, subnet)
    assert "allow 192.168.1.0/24;" in site
    assert "deny all;" in site
    assert "auth_basic_user_file" not in site
    assert "proxy_pass http://127.0.0.1:8090;" in site
    with pytest.raises(ValueError):
        mod.nginx_site("10.10.10.10","lan","192.168.1.0/24")
    policy.write_text("lan:0.0.0.0/0")
    with pytest.raises(ValueError):
        mod.read_access_policy(tmp_path)