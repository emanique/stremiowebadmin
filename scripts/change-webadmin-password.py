#!/usr/bin/env python3
"""Rotate the WebAdmin HTTP Basic Auth credential without storing plaintext."""
from __future__ import annotations

import getpass
import grp
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

ROOT = Path("/etc/stremio-webadmin")
CREDENTIAL = ROOT / "webadmin.htpasswd"

def rotate() -> None:
    if os.geteuid() != 0:
        raise PermissionError("run as root")
    if CREDENTIAL.is_symlink() or not CREDENTIAL.is_file():
        raise RuntimeError("WebAdmin credential missing; run the setup first")
    if not CREDENTIAL.read_text().startswith("admin:$6$"):
        raise RuntimeError("unexpected credential format")
    if os.isatty(0):
        password = getpass.getpass("New administrator password: ")
        confirm = getpass.getpass("Confirm administrator password: ")
        if password != confirm:
            raise ValueError("password confirmation mismatch")
        if len(password) < 20:
            raise ValueError("use at least 20 characters")
    else:
        password = secrets.token_urlsafe(30)
    h = subprocess.run(["openssl", "passwd", "-6", "-stdin"],
                       input=password + "\n",capture_output=True,
                       text=True,check=True).stdout.strip()
    if not h.startswith("$6$"):
        raise ValueError("invalid password hash")
    fd, name = tempfile.mkstemp(prefix=".webadmin-password-",dir=ROOT)
    try:
        os.fchmod(fd,0o640)
        os.fchown(fd,0,grp.getgrnam("www-data").gr_gid)
        with os.fdopen(fd,"w") as stream:
            stream.write("admin:"+h+"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name,CREDENTIAL)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    subprocess.run(["nginx","-t"],check=True)
    subprocess.run(["systemctl","reload","nginx"],check=True)
    print("Administrator password updated.")
    if not os.isatty(0):
        print("One-time administrator password: "+password)

if __name__=="__main__":
    rotate()
