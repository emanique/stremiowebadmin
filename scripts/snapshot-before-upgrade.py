#!/usr/bin/env python3
"""Consistent private backup of the .200 Stremio deployment before upgrade.

Requires the existing Core and WebAdmin containers STOPPED. No network and
no GitHub access. Includes credentials only inside the local private backup.
Use --exclude-cache only if the media cache is disposable and documented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE).strip()


def digest(path: pathlib.Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def backup(args: argparse.Namespace) -> int:
    if args.target_ip not in run("hostname", "-I").split():
        raise RuntimeError("refusing to snapshot the wrong host")
    names = ("stremio-libtorrent-server", "stremio-webadmin")
    containers = json.loads(run("docker", "inspect", *names))
    if any(x["State"]["Running"] for x in containers):
        raise RuntimeError("stop BOTH containers before snapshot; no live/inconsistent archive")
    if len({(x["Config"].get("Labels") or {}).get("com.docker.compose.project")
            for x in containers}) != 1:
        raise RuntimeError("containers are not from one Compose project")
    root = pathlib.Path(args.destination or (
        "/var/backups/stremio-upgrade-" +
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    ))
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(root, 0o700)
    manifest: dict = {"target_ip": args.target_ip, "archives": {},
                      "container_images": {}, "config_files": []}
    required = {
        "stremio-libtorrent-server": {
            "/root/.stremio-server": "media-cache",
            "/config": "core-config",
        },
        "stremio-webadmin": {
            "/data": "webadmin-data",
            "/config": "webadmin-config",
        },
    }
    sources: dict[str, pathlib.Path] = {}
    try:
        for item in containers:
            name = item["Name"].removeprefix("/")
            manifest["container_images"][name] = {
                "tag": item["Config"]["Image"], "image_id": item["Image"]
            }
            for mount in item.get("Mounts", []):
                target = mount["Destination"]
                label = required[name].get(target)
                if label:
                    source = pathlib.Path(mount["Source"])
                    if not source.is_dir() or source.is_symlink():
                        raise RuntimeError(f"invalid mount source for {label}")
                    sources[label] = source
        for required_label in ("media-cache", "core-config", "webadmin-data", "webadmin-config"):
            if required_label not in sources:
                raise RuntimeError(f"critical persistent path missing: {required_label}")
        workdirs = {
            (item["Config"].get("Labels") or {}).get(
                "com.docker.compose.project.working_dir") for item in containers
        }
        workdirs.discard(None)
        if len(workdirs) != 1:
            raise RuntimeError("cannot identify original Compose checkout")
        checkout = pathlib.Path(workdirs.pop())
        if not checkout.is_dir():
            raise RuntimeError("Compose checkout missing")
        for filename in (".env", "compose.yaml", "compose.vaapi.yaml",
                         "compose.gpu.yaml", "start.sh"):
            path = checkout / filename
            if path.is_file() and not path.is_symlink():
                dest = root / "source" / filename
                dest.parent.mkdir(mode=0o700, exist_ok=True)
                shutil.copy2(path, dest)
                os.chmod(dest, 0o600)
                manifest["config_files"].append(filename)
        for filename in ("stremio-webadmin", "stremio-addon-qa250"):
            path = pathlib.Path("/etc/nginx/sites-available") / filename
            if path.is_file() and not path.is_symlink():
                dest = root / "nginx" / filename
                dest.parent.mkdir(mode=0o700, exist_ok=True)
                shutil.copy2(path, dest)
                os.chmod(dest, 0o600)
        # Deduplicate /config, which is mounted in both containers.
        dedup: set[str] = set()
        for label in ("core-config", "webadmin-data",
                      "media-cache" if not args.exclude_cache else "core-config"):
            source = sources[label]
            resolved = str(source.resolve())
            if resolved in dedup:
                continue
            dedup.add(resolved)
            path = root / f"{label}.tar"
            # GNU tar sparse mode avoids copying unused blocks of growing
            # torrent files. Preserve owner, ACLs, extended attributes.
            with path.open("xb") as stream:
                subprocess.run(
                    ["tar", "--sparse", "--acls", "--xattrs", "--numeric-owner",
                     "-C", str(source), "-cpf", "-", "."],
                    stdout=stream, check=True, stderr=subprocess.PIPE,
                )
            os.chmod(path, 0o600)
            manifest["archives"][label] = {
                "name": path.name, "sha256": digest(path),
                "bytes": path.stat().st_size
            }
            print(f"[backup] archived {label} ({path.stat().st_size} bytes)")
        result = root / "manifest.json"
        result.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.chmod(result, 0o600)
        print(f"[backup] completed: {root}")
        print("[backup] contains private data; do not upload to GitHub")
        return 0
    except Exception:
        print(f"[backup] INCOMPLETE: {root}; do not use for rollback", file=sys.stderr)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-ip", default="192.168.1.200")
    parser.add_argument("--destination", type=str)
    parser.add_argument("--exclude-cache", action="store_true")
    try:
        sys.exit(backup(parser.parse_args()))
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"ABORT: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(3)
