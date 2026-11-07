#!/usr/bin/env python3
"""Synchronise only the auto-detected host IP into Compose .env, atomically."""
from __future__ import annotations
import ipaddress
import os
from pathlib import Path
import sys
import tempfile


def update_ip(original: str, address: str) -> str:
    ipaddress.IPv4Address(address)
    lines = original.splitlines()
    matches = 0
    result: list[str] = []
    for line in lines:
        if line.startswith("IPADDRESS="):
            matches += 1
            result.append("IPADDRESS=" + address)
        else:
            result.append(line)
    if matches > 1:
        raise ValueError("duplicate IPADDRESS in .env")
    if matches == 0:
        result.append("IPADDRESS=" + address)
    updated = "\n".join(result).rstrip("\n") + "\n"
    return original if original == updated else updated


def persist(path: Path, address: str) -> bool:
    if path.is_symlink() or not path.is_file():
        raise ValueError("refusing non-file or symlink environment")
    original = path.read_text()
    updated = update_ip(original, address)
    if updated == original:
        return False
    stat = path.stat()
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".host-env-")
    try:
        os.fchmod(fd, stat.st_mode & 0o777)
        os.fchown(fd, stat.st_uid, stat.st_gid)
        with os.fdopen(fd, "w") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(dir_fd)
        finally: os.close(dir_fd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return True


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: sync-host-env.py .env HOST_IPV4")
    changed = persist(Path(sys.argv[1]), sys.argv[2])
    print("host IP persisted" if changed else "host IP unchanged")
