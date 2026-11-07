"""DNS discovery must work on Debian without systemd-resolved."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/sync-host-dns.py"
spec = importlib.util.spec_from_file_location("sync_host_dns_standalone_test", SCRIPT)
dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dns)


def test_reject_loopback_and_linklocal_resolvers():
    assert dns.usable_dns("127.0.0.53") is None
    assert dns.usable_dns("169.254.1.1") is None
    assert dns.usable_dns("0.0.0.0") is None
    assert dns.usable_dns("224.0.0.1") is None
    assert dns.usable_dns("192.168.1.231") == "192.168.1.231"


def test_extract_real_nameservers_only():
    assert dns.configured_upstreams(
        "# nameserver 1.2.3.4\nnameserver 127.0.0.53\n"
        "nameserver 192.168.1.1 # local LAN\nnameserver 1.1.1.1\n"
    ) == ["192.168.1.1", "1.1.1.1"]


def test_resolvectl_fallback_uses_real_upstream():
    def cmd(args, **_kwargs):
        if args[0] == "ip":
            return '[{"dev":"ens18","gateway":"192.168.1.1"}]'
        raise FileNotFoundError("resolvectl is not installed")

    def read(path):
        return "nameserver 127.0.0.53\nnameserver 192.168.1.231\n"

    with patch.object(dns.subprocess, "check_output", side_effect=cmd), patch.object(
        Path, "read_text", lambda self, **kw: read(self)
    ):
        assert dns.discover() == "192.168.1.231"


def test_no_only_stub_is_accepted():
    def cmd(args, **_kwargs):
        if args[0] == "ip":
            return '[{"dev":"ens18"}]'
        raise FileNotFoundError()
    with patch.object(dns.subprocess, "check_output", side_effect=cmd), patch.object(
        Path, "read_text", return_value="nameserver 127.0.0.53\n"
    ):
        try:
            dns.discover()
        except RuntimeError as exc:
            assert "no usable host DNS" in str(exc)
        else:
            raise AssertionError("Stub-only resolver should be refused")


def test_persist_is_idempotent_and_preserves_permissions(tmp_path):
    file = tmp_path / ".env"
    file.write_text("HELLO=value\nSTREMIO_DNS_SERVER=192.168.1.1\n")
    file.chmod(0o600)
    assert dns.persist(file, "192.168.1.231")
    assert not dns.persist(file, "192.168.1.231")
    assert file.stat().st_mode & 0o777 == 0o600
    assert "HELLO=value" in file.read_text()


def test_persist_refuses_symlinks(tmp_path):
    target = tmp_path / "target"
    target.write_text("DATA=1\n")
    link = tmp_path / ".env"
    link.symlink_to(target)
    try:
        dns.persist(link, "1.1.1.1")
    except ValueError:
        pass
    else:
        raise AssertionError("Symlink .env must be refused")
    assert target.read_text() == "DATA=1\n"