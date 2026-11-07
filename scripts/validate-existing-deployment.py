#!/usr/bin/env python3
"""Refuse package setup that would silently overwrite an active Stremio binding."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys

def env_values(env: Path)->dict[str,str]:
    if not env.is_file() or env.is_symlink():
        raise RuntimeError("Missing or untrusted installation .env")
    values={}
    for row in env.read_text().splitlines():
        if row.strip().startswith("#") or "=" not in row:continue
        key,value=row.split("=",1)
        if key in values:raise RuntimeError(f"Duplicate {key} in .env")
        values[key]=value
    return values

def verify_existing(env: Path, inspect:dict|None=None)->str:
    if inspect is None:
        info=subprocess.run(["docker","inspect","stremio-libtorrent-server"],
                            text=True,capture_output=True,timeout=10)
        if info.returncode != 0:
            if any(marker in info.stderr.lower() for marker in ("no such object", "no such container")):
                return "fresh"
            raise RuntimeError("Cannot inspect the existing Stremio container")
        data=json.loads(info.stdout)
        if len(data)!=1:raise RuntimeError("Invalid Docker inspect response")
        inspect=data[0]
    existing={v.split("=",1)[0]:v.split("=",1)[1] for v in
              inspect["Config"]["Env"] if "=" in v}
    config=env_values(env)
    wanted_port=config.get("STREMIOSRV_BT_LISTEN_PORT","6881")
    actual_port=existing.get("STREMIOSRV_BT_LISTEN_PORT")
    if wanted_port!=actual_port:
        raise RuntimeError(
            f"Existing Stremio uses BT {actual_port}, package .env requests {wanted_port}. "
            "Migrate the existing host-owned .env before running setup.")
    saved_ip=config.get("IPADDRESS","")
    actual_ip=existing.get("IPADDRESS","")
    if saved_ip and actual_ip and saved_ip!=actual_ip:
        raise RuntimeError(
            f"Existing Stremio uses IP {actual_ip}, package .env requests {saved_ip}. "
            "Review host network migration before package setup.")
    return "existing-matched"

if __name__=="__main__":
    try:print("Deployment:",verify_existing(Path(sys.argv[1])))
    except Exception as exc:print("Setup blocked:",exc,file=sys.stderr);sys.exit(1)
