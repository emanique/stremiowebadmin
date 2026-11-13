"""Local IPTV addon: stable catalogue, secret isolation, signed per-channel jobs."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from stremiosrv.app import create_app
from stremiosrv.iptv import store

SAMPLE = """#EXTM3U
#EXTINF:-1 tvg-id="ch-one" group-title="Cinema",Canal Um
https://origin.example/live/one?password=SYNTHETIC-TEST-ONLY
#EXTINF:-1 tvg-id="ch-two" group-title="Desporto",Canal Dois
https://origin.example/live/two
"""


@pytest.fixture
def setup_iptv(tmp_path, monkeypatch):
    cfg = tmp_path / "iptv.json"
    cfg.write_text(json.dumps({
        "playlist_url": "https://catalog.example/channels.m3u",
        "addon_token": "t" * 48,
        "signing_secret": "s" * 64,
    }))
    cfg.chmod(0o600)
    monkeypatch.setenv("STREMIOSRV_IPTV_ENABLED", "true")
    monkeypatch.setenv("STREMIOSRV_IPTV_CONFIG", str(cfg))
    store.reset_cache()
    with patch.object(store, "_download", return_value=SAMPLE):
        yield
    store.reset_cache()


def test_stable_channel_ids_on_reordering():
    first = store.parse_m3u(SAMPLE)
    items = SAMPLE.split("#EXTINF:-1")
    second = store.parse_m3u(items[0] + "#EXTINF:-1" + items[2] + "#EXTINF:-1" + items[1])
    assert first.keys() == second.keys()
    assert len(first) == 2
    assert all(len(channel_id) == 24 for channel_id in first)


def test_parser_skips_unsafe_protocols():
    txt = SAMPLE + '#EXTINF:-1,Unsafe\nfile:///etc/shadow\n'
    assert len(store.parse_m3u(txt)) == 2


def test_addon_manifest_and_catalogue_hide_provider_credentials(setup_iptv):
    app = TestClient(create_app(), client=("192.168.1.41", 5999))
    for path in ("/iptv/" + "t" * 48 + "/manifest.json",
                 "/iptv/" + "t" * 48 + "/catalog/tv/channels.json"):
        response = app.get(path)
        assert response.status_code == 200, response.text
        assert "SYNTHETIC-TEST-ONLY" not in response.text
        assert "origin.example" not in response.text
    manifest = app.get("/iptv/" + "t" * 48 + "/manifest.json").json()
    assert manifest["types"] == ["tv"]
    rows = app.get("/iptv/" + "t" * 48 + "/catalog/tv/channels.json").json()["metas"]
    assert len(rows) == 2


def test_signed_stream_url_contains_only_core_host(setup_iptv):
    app = TestClient(create_app(), client=("192.168.1.41", 5999))
    channel_id = next(iter(store.channels()))
    result = app.get(f"/iptv/{'t' * 48}/stream/tv/iptv:{channel_id}.json")
    assert result.status_code == 200, result.text
    url = result.json()["streams"][0]["url"]
    assert url.endswith("/master.m3u8")
    assert "/hlsv2/iptv-" in url
    assert "origin.example" not in url
    assert "SYNTHETIC-TEST-ONLY" not in url
    job = url.split("/hlsv2/")[1].split("/")[0]
    assert store.resolve_job(job).id == channel_id
    assert store.resolve_job(job[:-1] + ("a" if job[-1] != "a" else "b")) is None


def test_wrong_token_refused(setup_iptv):
    app = TestClient(create_app(), client=("192.168.1.41", 5999))
    assert app.get("/iptv/wrong/manifest.json").status_code == 404


def test_hls_master_signed_job_is_not_client_overrideable(setup_iptv):

    channel_id = next(iter(store.channels()))
    job = store.signing_job(channel_id)
    app = create_app()
    app.state.converter = object()
    c = TestClient(app, client=("192.168.1.41", 5999))
    result = c.get(f"/hlsv2/{job}/master.m3u8",
                   params={"mediaURL": "https://attacker.example/malicious"})
    assert result.status_code == 404
    assert c.get("/hlsv2/iptv-deadbeef-deadbeef/master.m3u8").status_code == 404


def test_reject_unprotected_config_permissions(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "playlist_url": "https://origin.example/m3u",
        "addon_token": "t" * 48, "signing_secret": "s" * 64,
    }))
    p.chmod(0o644)
    monkeypatch.setenv("STREMIOSRV_IPTV_CONFIG", str(p))
    with pytest.raises(ValueError, match="owner-only"):
        store._config()



def test_channel_switch_releases_only_unused_previous_job():
    from stremiosrv.iptv import switches

    switches.reset()
    assert switches.switch("192.168.1.41", "iptv-a", now=1) is None
    assert switches.switch("192.168.1.42", "iptv-a", now=2) is None
    assert switches.switch("192.168.1.41", "iptv-b", now=3) is None
    assert switches.switch("192.168.1.42", "iptv-c", now=4) == "iptv-a"
    assert switches.switch("192.168.1.41", "iptv-b", now=5) is None
    assert switches.switch("192.168.1.41", "iptv-d", now=6) == "iptv-b"
    switches.reset()
