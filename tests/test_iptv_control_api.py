"""Core IPTV management is inaccessible without its server-only secret."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from stremiosrv.app import create_app
from stremiosrv.iptv import store


def test_internal_control_auth_and_refresh(tmp_path, monkeypatch):
    config = tmp_path / "iptv.json"
    config.write_text(json.dumps({
        "playlist_url": "https://source.example/channels.m3u",
        "addon_token": "a" * 48,
        "signing_secret": "s" * 64,
        "enabled": True,
    }))
    config.chmod(0o600)
    monkeypatch.setenv("STREMIOSRV_IPTV_ENABLED", "true")
    monkeypatch.setenv("STREMIOSRV_IPTV_CONFIG", str(config))
    monkeypatch.setattr(store, "_download", lambda _url: (
        "#EXTM3U\n#EXTINF:-1,Canal 1\nhttps://media.example/a\n"
        "#EXTINF:-1,Canal 2\nhttps://media.example/b\n"
    ))
    store.reset_cache()
    c = TestClient(create_app(), client=("172.18.0.5", 1234))
    assert c.get("/_iptv-admin/status").status_code == 404
    headers = {"X-IPTV-Secret": "s" * 64}
    assert c.get("/_iptv-admin/status", headers=headers).status_code == 200
    r = c.post("/_iptv-admin/test", headers=headers, json={
        "url": "https://source.example/changed.m3u"})
    assert r.status_code == 200
    assert r.json()["count"] == 2
    assert "media.example" not in r.text
    r = c.post("/_iptv-admin/refresh", headers=headers)
    assert r.status_code == 200
    assert r.json()["count"] == 2
    r = c.get("/_iptv-admin/channels?q=canal&limit=1", headers=headers)
    assert r.status_code == 200 and r.json()["total"] == 2
    assert len(r.json()["items"]) == 1
    assert "media.example" not in r.text
    store.reset_cache()


def test_disabling_addon_requires_no_restart(tmp_path, monkeypatch):
    config = tmp_path / "iptv.json"
    data = {
        "playlist_url": "https://source.example/channels.m3u",
        "addon_token": "a" * 48,
        "signing_secret": "s" * 64,
        "enabled": True,
    }
    config.write_text(json.dumps(data))
    config.chmod(0o600)
    monkeypatch.setenv("STREMIOSRV_IPTV_CONFIG", str(config))
    monkeypatch.setenv("STREMIOSRV_IPTV_ENABLED", "true")
    c = TestClient(create_app(), client=("192.168.1.41", 1234))
    route = "/iptv/" + data["addon_token"] + "/manifest.json"
    assert c.get(route).status_code == 200
    data["enabled"] = False
    config.write_text(json.dumps(data))
    assert c.get(route).status_code == 404
    data["enabled"] = True
    config.write_text(json.dumps(data))
    assert c.get(route).status_code == 200
