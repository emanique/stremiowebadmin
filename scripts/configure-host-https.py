#!/usr/bin/env python3
"""Provision HTTPS for WebAdmin on the *current host IPv4*, not a fixed DEV IP."""
from __future__ import annotations

import argparse
import grp
import ipaddress
import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path


def execute(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def detect_ip() -> str:
    result = execute("ip", "-4", "route", "get", os.environ.get("IP_DETECT_TARGET", "1.1.1.1")).stdout
    match = re.search(r"\bsrc (\d+\.\d+\.\d+\.\d+)\b", result)
    if not match:
        raise RuntimeError("cannot identify the current host IPv4")
    value = ipaddress.IPv4Address(match.group(1))
    if value.is_loopback or value.is_unspecified:
        raise RuntimeError("invalid host address")
    return str(value)


def read_access_policy(base: Path) -> tuple[str, str | None]:
    """Explicit local opt-in; secure Basic auth remains the default."""
    policy = base / "access-policy"
    if not policy.exists():
        return "basic", None
    if policy.is_symlink() or not policy.is_file():
        raise ValueError("access-policy must be a regular file")
    value = policy.read_text(encoding="ascii").strip()
    if value == "basic":
        return "basic", None
    if not value.startswith("lan:"):
        raise ValueError("access-policy must be basic or lan:IPV4_CIDR")
    network = ipaddress.ip_network(value[4:], strict=True)
    if network.version != 4 or not network.is_private or network.prefixlen < 24:
        raise ValueError("LAN mode requires a private IPv4 CIDR with /24 or narrower")
    return "lan", str(network)


def nginx_site(ip: str, auth_mode: str = "basic", trusted_cidr: str | None = None) -> str:
    address = ipaddress.IPv4Address(ip)
    if auth_mode == "basic":
        auth_directives = '        auth_basic "Stremio WebAdmin";\\n        auth_basic_user_file /etc/stremio-webadmin/webadmin.htpasswd;'
    elif auth_mode == "lan":
        if not trusted_cidr:
            raise ValueError("LAN mode needs explicit trusted_cidr")
        net = ipaddress.IPv4Network(trusted_cidr, strict=True)
        if not net.is_private or net.prefixlen < 24 or address not in net:
            raise ValueError("Trusted LAN network must include host IP and be private /24 or narrower")
        auth_directives = f"        allow {net};\\n        deny all;"
    else:
        raise ValueError("Invalid authentication mode")
    return f"""# Generated for current host IPv4 by StremioServerWebAdmin.
# WebPlayer streaming endpoints are independent (8080, 11470, 12470).
server {{
    listen {ip}:443 ssl;
    server_name {ip};
    ssl_certificate /etc/stremio-webadmin/webadmin.crt;
    ssl_certificate_key /etc/stremio-webadmin/webadmin.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_tickets off;
    server_tokens off;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy same-origin always;
    add_header Cache-Control "no-store" always;
    client_max_body_size 2m;
    location / {{
{auth_directives}
        proxy_pass http://127.0.0.1:8090;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 240s;
        proxy_set_header Connection "";
    }}
}}
"""


def certificate_matches(cert: Path, ip: str) -> bool:
    if not cert.is_file():
        return False
    proc = subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-checkip", ip],
                          capture_output=True, text=True)
    return proc.returncode == 0


def ensure_admin_auth(base: Path) -> None:
    """Fail closed: Nginx workers must only read the password hash.

    The initial password is printed exactly once and never written to disk.
    Future setup/reconciliation runs preserve the existing password hash.
    """
    path = base / "webadmin.htpasswd"
    try:
        www_data = grp.getgrnam("www-data").gr_gid
    except KeyError as error:
        raise RuntimeError("Nginx www-data group is required") from error
    if path.is_symlink():
        raise RuntimeError("refusing symlinked WebAdmin password file")
    if path.exists():
        if not path.is_file() or not path.read_text().startswith("admin:$6$"):
            raise RuntimeError("existing WebAdmin credential is invalid")
    else:
        password = secrets.token_urlsafe(30)
        hashed = execute("openssl", "passwd", "-6", "-stdin",
                         input=password + "\n").stdout.strip()
        if not hashed.startswith("$6$"):
            raise RuntimeError("OpenSSL did not produce SHA-512 password hash")
        fd, staged = tempfile.mkstemp(prefix=".webadmin-password-", dir=base)
        try:
            os.fchmod(fd, 0o640)
            os.fchown(fd, 0, www_data)
            with os.fdopen(fd, "w") as handle:
                handle.write("admin:" + hashed + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staged, path)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)
        print("New Stremio WebAdmin administrator credentials.")
        print("Username: admin")
        print("Initial password (shown only once): " + password)
        print("Store it securely. To rotate: sudo python3 scripts/change-webadmin-password.py")
    # Permit Nginx workers to traverse the directory, not to read private keys.
    os.chown(base, 0, www_data)
    os.chmod(base, 0o750)
    os.chown(path, 0, www_data)
    os.chmod(path, 0o640)


def provision(ip: str) -> None:
    if os.geteuid() != 0:
        raise PermissionError("run the HTTPS host installer as root")
    base = Path("/etc/stremio-webadmin")
    base.mkdir(mode=0o700, exist_ok=True)
    mode, subnet = read_access_policy(base)
    if mode == "basic":
        ensure_admin_auth(base)
    cert, key = base / "webadmin.crt", base / "webadmin.key"
    if not certificate_matches(cert, ip) or not key.is_file():
        with tempfile.TemporaryDirectory(dir=base) as temp:
            generated = Path(temp)
            execute(
                "openssl", "req", "-x509", "-newkey", "rsa:3072", "-sha256",
                "-nodes", "-days", "365",
                "-subj", f"/CN={ip}/O=StremioServerWebAdmin",
                "-addext", f"subjectAltName=IP:{ip}",
                "-addext", "extendedKeyUsage=serverAuth",
                "-keyout", str(generated / "key.pem"),
                "-out", str(generated / "cert.pem"),
            )
            os.chmod(generated / "key.pem", 0o600)
            os.replace(generated / "key.pem", key)
            os.replace(generated / "cert.pem", cert)
    site = Path("/etc/nginx/sites-available/stremio-webadmin")
    site.parent.mkdir(parents=True, exist_ok=True)
    previous = site.read_text() if site.is_file() else None
    site.write_text(nginx_site(ip, mode, subnet))
    enabled = Path("/etc/nginx/sites-enabled/stremio-webadmin")
    enabled.parent.mkdir(parents=True, exist_ok=True)
    if enabled.is_symlink() or enabled.is_file():
        enabled.unlink()
    enabled.symlink_to(site)
    try:
        execute("nginx", "-t")
        execute("systemctl", "enable", "--now", "nginx")
        execute("systemctl", "reload", "nginx")
    except (subprocess.CalledProcessError, OSError):
        if previous is None:
            site.unlink(missing_ok=True)
            enabled.unlink(missing_ok=True)
        else:
            site.write_text(previous)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="configure host Nginx and IP SAN TLS")
    args = parser.parse_args()
    address = detect_ip()
    print(f"detected host: {address}, WebAdmin: https://{address}/")
    if args.apply:
        provision(address)
        print("HTTPS host configuration applied")
    else:
        print("check only; use --apply for first installation or when host IPv4 changes")
