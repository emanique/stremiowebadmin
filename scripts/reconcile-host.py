#!/usr/bin/env python3
"""Host-only reconciliation. No VPN Gateway policy changes; consume its API."""
from __future__ import annotations
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
LOCK=Path("/run/lock/stremio-webadmin-host-reconcile.lock")

def command(*args: str, timeout: int = 180) -> str:
    return subprocess.run(args, check=True, text=True, capture_output=True,
                          cwd=ROOT, timeout=timeout).stdout

def snapshot() -> tuple[str,str]:
    route=json.loads(command("ip","-j","-4","route","get","1.1.1.1",timeout=10))
    if len(route)!=1 or not route[0].get("dev") or not route[0].get("prefsrc"):
        raise RuntimeError("ambiguous host egress route")
    ip=str(ipaddress.IPv4Address(route[0]["prefsrc"]))
    dns=command(sys.executable,str(ROOT/"scripts/sync-host-dns.py"),
                str(ROOT/".env"),timeout=10) # DNS sync is idempotent.
    return ip,dns

def env_value(key: str) -> str|None:
    for line in (ROOT/".env").read_text().splitlines():
        if line.startswith(key+"="):
            return line.partition("=")[2]
    return None

def reconcile() -> int:
    if not (ROOT/".env").is_file():
        raise RuntimeError("run stremio-webadmin-setup first")
    before_ip=env_value("IPADDRESS")
    before_dns=env_value("STREMIO_DNS_SERVER")
    ip,_=snapshot()
    after_dns=env_value("STREMIO_DNS_SERVER")
    ip_auto=env_value("IPADDRESS_SOURCE")=="auto"
    if ip_auto and before_ip!=ip:
        command(sys.executable,str(ROOT/"scripts/sync-host-env.py"),
                str(ROOT/".env"),ip,timeout=10)
        command(sys.executable,str(ROOT/"scripts/configure-host-https.py"),
                "--apply",timeout=35)
    network_changed=(ip_auto and before_ip!=ip) or (before_dns!=after_dns)
    # Only deployed containers need recreation; no local build or cache deletion.
    if network_changed:
        command("sh","start.sh","up","-d","--no-build","--force-recreate",timeout=180)
        print("host networking reconciled",flush=True)
    # Boot GPU and port bindings may change without DNS/IP changes.
    command(sys.executable,str(ROOT/"scripts/boot-gpu-reconcile.py"),timeout=170)
    # Gateway port state is read only; a missing host token remains unconfigured.
    result=subprocess.run([sys.executable,str(ROOT/"scripts/gateway-port-sync.py")],
                          cwd=ROOT,timeout=170,text=True,capture_output=True)
    if result.returncode:
        print("Gateway port status:",result.stdout.strip(),result.stderr.strip(),
              file=sys.stderr)
    return 0

if __name__=="__main__":
    LOCK.parent.mkdir(parents=True,exist_ok=True)
    with LOCK.open("a+") as handle:
        try:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(0)
        try:sys.exit(reconcile())
        except Exception as error:
            print("host reconciliation failed:",error,file=sys.stderr)
            sys.exit(1)
