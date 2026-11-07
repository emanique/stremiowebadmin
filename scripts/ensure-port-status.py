#!/usr/bin/env python3
"""Ensure the host bind for Gateway status is always a regular JSON file."""
from __future__ import annotations
import json
import os
from pathlib import Path

STATUS=Path("/var/lib/stremio-webadmin/port-status.json")

def ensure_status(path: Path=STATUS)->bool:
    path.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError(f"{path} is not a regular file; stop WebAdmin and repair the host mount")
    if path.exists():
        json.loads(path.read_text())
        return False
    descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o644)
    with os.fdopen(descriptor,"w") as f:
        json.dump({"result":"unconfigured","detail":"Gateway host policy not provisioned"},f)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    return True

if __name__=="__main__":
    print("Gateway status:", "created" if ensure_status() else "already present")
