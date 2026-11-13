"""WebAdmin IPTV first-run and channel preference API regression.

No real provider URL, token, user, password or M3U file is used.
"""
from __future__ import annotations

import importlib.util
import json
import stat
import sys
import types
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _api(tmp_path, monkeypatch):
    module = types.ModuleType("config_reliability")
    module.app = FastAPI()
    monkeypatch.setitem(sys.modules, "config_reliability", module)
    path = Path(__file__).resolve().parents[1] / "webadmin" / "iptv_management.py"
    spec = importlib.util.spec_from_file_location("iptv_management_testonly", path)
    implementation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(implementation)
    monkeypatch.setattr(implementation, "IPTV_CONFIG", tmp_path / "iptv-local.json")
    monkeypatch.setattr(
        implementation, "IPTV_PREFERENCES", tmp_path / "iptv-channel-preferences.json"
    )
    return implementation, TestClient(module.app)


def test_new_install_has_no_provider_credentials_or_playlist(tmp_path, monkeypatch):
    implementation, client = _api(tmp_path, monkeypatch)
    response = client.get("/api/iptv")
    assert response.status_code == 200
    data = response.json()
    assert not data["configured"]
    assert not data["enabled"]
    assert data["channels"] == 0
    private = json.loads(implementation.IPTV_CONFIG.read_text())
    assert private["playlist_url"] == ""
    assert len(private["addon_token"]) >= 32
    assert len(private["signing_secret"]) >= 40
    assert private["enabled"] is False
    assert stat.S_IMODE(implementation.IPTV_CONFIG.stat().st_mode) == 0o600
    assert "addon_token" not in data
    assert "signing_secret" not in data


def test_first_url_test_stages_then_restores_empty_config(tmp_path, monkeypatch):
    implementation, client = _api(tmp_path, monkeypatch)
    observed = []
    def internal(path, cfg, payload=None):
        observed.append((path, cfg.get("playlist_url"), payload))
        assert cfg["playlist_url"] == "https://synthetic.example.net/channels.m3u8"
        return {"ok": True, "count": 3}
    monkeypatch.setattr(implementation, "_internal", internal)
    response = client.post("/api/iptv/test", json={
        "url": "https://synthetic.example.net/channels.m3u8"
    })
    assert response.status_code == 200
    assert response.json()["count"] == 3
    assert observed
    stored = json.loads(implementation.IPTV_CONFIG.read_text())
    assert stored["playlist_url"] == ""


def test_single_channel_hide_rank_restore_persist_without_url_leak(tmp_path, monkeypatch):
    implementation, client = _api(tmp_path, monkeypatch)
    first = client.get("/api/iptv")
    assert first.status_code == 200
    cid = "a" * 24
    for action, rank in [("rank", 7), ("hide", None)]:
        payload = {"action": action, "channel_id": cid}
        if rank is not None:
            payload["rank"] = rank
        response = client.post("/api/iptv/channel-preferences", json=payload)
        assert response.status_code == 200, response.text
    prefs = json.loads(implementation.IPTV_PREFERENCES.read_text())
    assert prefs["excluded"] == [cid]
    assert prefs["order"] == {cid: 7}
    assert stat.S_IMODE(implementation.IPTV_PREFERENCES.stat().st_mode) == 0o600
    assert "playlist" not in implementation.IPTV_PREFERENCES.read_text()
    response = client.post("/api/iptv/channel-preferences", json={
        "action": "restore", "channel_id": cid
    })
    assert response.status_code == 200
    assert json.loads(implementation.IPTV_PREFERENCES.read_text())["excluded"] == []


def test_invalid_channel_id_and_order_are_rejected(tmp_path, monkeypatch):
    _, client = _api(tmp_path, monkeypatch)
    bad = client.post("/api/iptv/channel-preferences", json={
        "action": "hide", "channel_id": "../config/iptv-local.json"
    })
    assert bad.status_code == 422
    no_rank = client.post("/api/iptv/channel-preferences", json={
        "action": "rank", "channel_id": "a" * 24
    })
    assert no_rank.status_code == 422


def test_upload_private_m3u8_is_not_returned_to_browser(tmp_path, monkeypatch):
    implementation, client = _api(tmp_path, monkeypatch)
    monkeypatch.setattr(implementation, "IPTV_UPLOAD", tmp_path / "iptv-upload.m3u")
    sample = ("#EXTM3U\n#EXTINF:-1 tvg-chno=\\\"2\\\",Canal Privado\n"
              "https://synthetic.example.net/stream?login=FAKE&password=FAKE\n")
    def internal(path, cfg, data=None):
        assert path == "refresh"
        assert cfg["playlist_source"] == "file"
        assert implementation.IPTV_UPLOAD.read_text() == sample
        return {"ok": True, "count": 1}
    monkeypatch.setattr(implementation, "_internal", internal)
    response = client.post("/api/iptv/upload", json={"name": "canais.m3u8", "text": sample})
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    assert "synthetic.example.net" not in response.text
    assert "password" not in response.text
    assert stat.S_IMODE(implementation.IPTV_UPLOAD.stat().st_mode) == 0o600
    cfg = json.loads(implementation.IPTV_CONFIG.read_text())
    assert cfg["playlist_source"] == "file"
    assert cfg["playlist_url"] == ""


def test_invalid_master_hls_upload_refused(tmp_path, monkeypatch):
    implementation, client = _api(tmp_path, monkeypatch)
    monkeypatch.setattr(implementation, "IPTV_UPLOAD", tmp_path / "iptv-upload.m3u")
    result = client.post("/api/iptv/upload", json={
        "name": "master.m3u8",
        "text": "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nhttps://example.net/a.m3u8\n",
    })
    assert result.status_code == 422
    assert not implementation.IPTV_UPLOAD.exists()


def test_failed_upload_restores_previous_config_and_file(tmp_path, monkeypatch):
    from fastapi import HTTPException
    implementation, client = _api(tmp_path, monkeypatch)
    monkeypatch.setattr(implementation, "IPTV_UPLOAD", tmp_path / "iptv-upload.m3u")
    client.get("/api/iptv")
    old = implementation.IPTV_CONFIG.read_bytes()
    def fail(path, cfg, data=None):
        raise HTTPException(status_code=422, detail="invalid channel list")
    monkeypatch.setattr(implementation, "_internal", fail)
    response = client.post("/api/iptv/upload", json={
        "name": "canais.m3u",
        "text": "#EXTM3U\n#EXTINF:-1,Canal\nhttps://example.net/a\n",
    })
    assert response.status_code == 422
    assert implementation.IPTV_CONFIG.read_bytes() == old
    assert not implementation.IPTV_UPLOAD.exists()


def test_uploaded_iptv_catalogue_with_ext_x_session_data(tmp_path, monkeypatch):
    implementation, client = _api(tmp_path, monkeypatch)
    monkeypatch.setattr(implementation, "IPTV_UPLOAD", tmp_path / "iptv-upload.m3u")
    playlist = (
        '#EXTM3U\n'
        '#EXT-X-SESSION-DATA:DATA-ID="com.example.test",VALUE="demo"\n'
        '#EXTINF:-1 tvg-chno="7",Channel Seven\n'
        'https://synthetic.example.net/channel/7.m3u8\n'
    )
    assert not implementation._is_hls_manifest(playlist)
    def fake_refresh(path, config, data=None):
        assert path == "refresh"
        assert implementation.IPTV_UPLOAD.read_text() == playlist
        return {"ok": True, "count": 1}
    monkeypatch.setattr(implementation, "_internal", fake_refresh)
    response = client.post("/api/iptv/upload", json={"name": "test.m3u", "text": playlist})
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1


def test_core_playlist_validation_status_is_not_mislabeled_as_502(tmp_path, monkeypatch):
    import httpx
    implementation, client = _api(tmp_path, monkeypatch)
    class RejectedCore:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            request = httpx.Request("POST", "http://core/_iptv-admin/test")
            response = httpx.Response(422, request=request)
            raise httpx.HTTPStatusError("rejected", request=request, response=response)

    # Replace only the outbound client factory; do not monkeypatch the
    # inherited httpx.Client.post used by FastAPI's TestClient itself.
    monkeypatch.setattr(implementation.httpx, "Client", RejectedCore)
    response = client.post("/api/iptv/test", json={"url": "https://synthetic.example.net/invalid.m3u"})
    assert response.status_code == 422
    assert "Core" in response.json()["detail"]
