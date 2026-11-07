#!/usr/bin/env python3
"""Discover a host DNS server usable by Docker without forcing systemd-resolved.

Use per-interface systemd-resolved DNS when available. On minimal Debian hosts
without resolvectl, use an actual upstream nameserver from /etc/resolv.conf.
Never pass loopback/stub, unspecified, multicast or link-local resolvers into
a container. Fail closed when only stub resolvers are available.
"""
from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def usable_dns(address: str) -> str | None:
    try:
        ipv4 = ipaddress.IPv4Address(address.split("%", 1)[0])
    except (ValueError, TypeError):
        return None
    if ipv4.is_loopback or ipv4.is_multicast or ipv4.is_unspecified or ipv4.is_link_local:
        return None
    if ipv4 == ipaddress.IPv4Address("255.255.255.255"):
        return None
    return str(ipv4)


def configured_upstreams(config: str) -> list[str]:
    found = []
    for line in config.splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) == 2 and parts[0] == "nameserver":
            address = usable_dns(parts[1])
            if address and address not in found:
                found.append(address)
    return found


def discover() -> str:
    route = json.loads(subprocess.check_output(
        ["ip", "-j", "-4", "route", "get", "1.1.1.1"], text=True))
    if len(route) != 1 or not route[0].get("dev"):
        raise RuntimeError("no host egress interface")

    try:
        info = subprocess.check_output(
            ["resolvectl", "dns", route[0]["dev"]], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        info = ""

    if ":" in info:
        for entry in info.partition(":")[2].split():
            address = usable_dns(entry)
            if address:
                return address

    for candidate in (Path("/run/systemd/resolve/resolv.conf"), Path("/etc/resolv.conf")):
        try:
            values = configured_upstreams(candidate.read_text())
        except OSError:
            continue
        if values:
            return values[0]

    raise RuntimeError("no usable host DNS: refusing Docker loopback/stub resolver")


def persist(path: Path, dns: str) -> bool:
    if path.is_symlink() or not path.is_file():
        raise ValueError("untrusted env")
    if not usable_dns(dns):
        raise ValueError("invalid upstream DNS")
    original = path.read_text()
    lines = original.splitlines()
    count = sum(line.startswith("STREMIO_DNS_SERVER=") for line in lines)
    if count > 1:
        raise ValueError("duplicate DNS settings")
    lines = [
        ("STREMIO_DNS_SERVER=" + dns) if line.startswith("STREMIO_DNS_SERVER=") else line
        for line in lines
    ]
    if not count:
        lines.append("STREMIO_DNS_SERVER=" + dns)
    updated = "\n".join(lines) + "\n"
    if updated == original:
        return False

    metadata = path.stat()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".dns-")
    try:
        os.fchmod(fd, metadata.st_mode & 0o777)
        os.fchown(fd, metadata.st_uid, metadata.st_gid)
        with os.fdopen(fd, "w") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


if __name__ == "__main__":
    upstream = discover()
    print("DNS", upstream, "changed", persist(Path(sys.argv[1]), upstream))