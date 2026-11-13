#!/usr/bin/env python3
"""Read-only inventory before replacing the .200 Stremio runtime.

Never reads playlist contents, token values or .env values and never logs
credentials. Run on the target host. Output can be attached to a change ticket.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path


def command(*args: str) -> str:
    return subprocess.check_output(args, stderr=subprocess.DEVNULL, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-ip", default="192.168.1.200")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    addresses = set(command("hostname", "-I").split())
    if args.target_ip not in addresses:
        print(f"ABORT: this host does not own {args.target_ip}", file=sys.stderr)
        return 3

    names = ("stremio-libtorrent-server", "stremio-webadmin")
    try:
        containers = json.loads(command("docker", "inspect", *names))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        print("ABORT: expected existing Stremio containers not found", file=sys.stderr)
        return 4

    report: dict = {"target_ip": args.target_ip, "host": socket.gethostname()}
    services = []
    critical = {}
    for item in containers:
        name = item["Name"].removeprefix("/")
        mounts = item.get("Mounts", [])
        binds = item.get("HostConfig", {}).get("PortBindings", {})
        service = {
            "name": name,
            "image_tag": item["Config"].get("Image"),
            "image_id": item["Image"],
            "status": item.get("State", {}).get("Status"),
            "health": (item.get("State", {}).get("Health") or {}).get("Status"),
            "restarts": item.get("RestartCount"),
            "created": item.get("Created"),
            "project": (item["Config"].get("Labels") or {}).get("com.docker.compose.project"),
            "project_working_dir": (
                item["Config"].get("Labels") or {}
            ).get("com.docker.compose.project.working_dir"),
            "port_bindings": {
                port: [x.get("HostIp", "") + ":" + x.get("HostPort", "")
                       for x in (mapping or [])]
                for port, mapping in binds.items()
            },
            "gpu_devices": [
                {"path": d.get("PathOnHost"), "inside": d.get("PathInContainer")}
                for d in (item["HostConfig"].get("Devices") or [])
            ],
            # No source paths or secret data are included in the report.
            "mounts": [
                {"type": x.get("Type"), "name": x.get("Name"), "target": x.get("Destination")}
                for x in mounts
            ],
        }
        services.append(service)
        if name == "stremio-libtorrent-server":
            critical["media_cache"] = any(
                x.get("Destination") == "/root/.stremio-server" for x in mounts)
            critical["core_config"] = any(
                x.get("Destination") == "/config" for x in mounts)
        if name == "stremio-webadmin":
            critical["webadmin_data"] = any(
                x.get("Destination") == "/data" for x in mounts)
            critical["webadmin_config"] = any(
                x.get("Destination") == "/config" for x in mounts)
    report["services"] = services
    report["persistent_mount_checks"] = critical
    report["disk"] = {}
    for mount in ("/", "/var/lib/docker", "/opt"):
        if Path(mount).exists():
            s = os.statvfs(mount)
            report["disk"][mount] = {
                "available_gib": round(s.f_bavail * s.f_frsize / (1024**3), 2),
                "total_gib": round(s.f_blocks * s.f_frsize / (1024**3), 2),
            }
    report["model_directories"] = []
    for directory in ("/opt/stremio-server-webadmin/models/subtitles",
                      "/opt/StremioServerWebAdmin/models/subtitles"):
        p = Path(directory)
        if p.is_dir():
            report["model_directories"].append(
                {"path": directory, "pairs": sorted(
                    q.name for q in p.iterdir()
                    if q.is_dir() and not q.is_symlink())}
            )
    # Avoid including any provider URLs, account identifiers or contents of .env.
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(output + "\n")
        print(f"Inventory written to {args.output}")
    else:
        print(output)

    if not all(critical.values()):
        print("ABORT: expected persistent volumes/mounts are missing", file=sys.stderr)
        return 5
    projects = {x["project"] for x in services}
    if None in projects or len(projects) != 1:
        print("ABORT: current containers are not in one Compose project", file=sys.stderr)
        return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
