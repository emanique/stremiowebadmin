"""Standalone WebAdmin must never ship unauthenticated Docker management."""
from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/configure-host-https.py"
spec=importlib.util.spec_from_file_location("host_https_auth",SCRIPT)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_https_auth_is_unconditional():
    site=module.nginx_site("192.0.2.10")
    assert 'auth_basic "Stremio WebAdmin";' in site
    assert "auth_basic_user_file /etc/stremio-webadmin/webadmin.htpasswd;" in site
    assert "proxy_pass http://127.0.0.1:8090;" in site
    assert "listen 192.0.2.10:443 ssl;" in site


def test_password_is_hashed_created_once_and_not_logged(tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    monkeypatch.setattr(module.grp,"getgrnam",lambda name:SimpleNamespace(gr_gid=0))
    monkeypatch.setattr(module.secrets,"token_urlsafe",lambda n:"once-only-secret-plaintext")
    monkeypatch.setattr(module,"execute",lambda *args,**kw:SimpleNamespace(stdout="$6$salt$fakehash\n"))
    module.ensure_admin_auth(tmp_path)
    credential=tmp_path/"webadmin.htpasswd"
    assert credential.read_text()=="admin:$6$salt$fakehash\n"
    assert stat.S_IMODE(credential.stat().st_mode)==0o640
    assert stat.S_IMODE(tmp_path.stat().st_mode)==0o750
    first=capsys.readouterr().out
    assert "once-only-secret-plaintext" in first
    assert "once-only-secret-plaintext" not in credential.read_text()
    module.ensure_admin_auth(tmp_path)
    assert capsys.readouterr().out==""
    assert credential.read_text()=="admin:$6$salt$fakehash\n"


def test_invalid_existing_password_file_rejected(tmp_path,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(module.grp,"getgrnam",lambda name:SimpleNamespace(gr_gid=0))
    (tmp_path/"webadmin.htpasswd").write_text("admin:plaintext\n")
    with pytest.raises(RuntimeError,match="invalid"):
        module.ensure_admin_auth(tmp_path)


def test_password_symlink_rejected(tmp_path,monkeypatch):
    target=tmp_path/"other"
    target.write_text("admin:$6$dummy\n")
    (tmp_path/"webadmin.htpasswd").symlink_to(target)
    with pytest.raises(RuntimeError,match="symlink"):
        module.ensure_admin_auth(tmp_path)
