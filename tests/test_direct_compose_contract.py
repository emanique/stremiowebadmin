"""Independent direct-network deployment contract (without Docker daemon state)."""
from pathlib import Path
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def render_compose():
    env = os.environ.copy()
    env["IPADDRESS"] = "192.168.1.221"
    env["STREMIOSRV_BT_LISTEN_PORT"] = "6881"  # independent of local Gateway allocation
    return json.loads(
        subprocess.check_output(
            ["docker", "compose", "-f", str(ROOT / "compose.yaml"), "config", "--format", "json"],
            cwd=ROOT,
            env=env,
            text=True,
        )
    )


def test_exact_services_and_no_gateway():
    services = render_compose()["services"]
    assert set(services) == {"stremio-libtorrent-server", "webadmin"}
    for s in services.values():
        assert not str(s.get("network_mode", "")).startswith("service:")
        assert "NET_ADMIN" not in s.get("cap_add", [])
        assert all("/dev/net/tun" not in str(d) for d in s.get("devices", []))


def test_ports_bound_to_host_ip():
    services = render_compose()["services"]
    core_ports = services["stremio-libtorrent-server"]["ports"]
    assert {p["target"] for p in core_ports} == {8080, 11470, 12470, 6881}
    assert all(p["host_ip"] == "192.168.1.221" for p in core_ports)
    assert services["webadmin"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert all("vpn" not in str(x).lower() for x in services["stremio-libtorrent-server"].get("volumes", []))


def test_webadmin_core_dns_and_ui():
    services = render_compose()["services"]
    assert services["webadmin"]["environment"]["STREMIO_URL"] == "http://stremio-libtorrent-server:11470"
    assert not any("gluetun" in str(item).lower() for item in services.values())
    assert not any("pihole" in str(item).lower() for item in services.values())
    ui = (ROOT / "webadmin/static/index.html").read_text()
    assert 'id="piholeAdmin"' not in ui


def test_admin_not_exposed_to_lan_by_default():
    """No authentication middleware yet: keep control plane on host loopback."""
    services = render_compose()["services"]
    admin = services["webadmin"]
    assert admin["ports"][0]["published"] == "8090"
    assert admin["ports"][0]["host_ip"] == "127.0.0.1"
    assert any("docker.sock" in str(mount) for mount in admin.get("volumes", []))
