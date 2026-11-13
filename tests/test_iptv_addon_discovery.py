"""IPTV entry in WebAdmin's existing addon selector.

All values here are synthetic. The manifest capability URL is intended for
the Stremio installation UI, whereas upstream credentials are never exposed.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


def _module(monkeypatch):
    fake = types.ModuleType("version_lifecycle")
    fake._connection_urls = lambda: {
        "trustedHttps": True, "ip": "192.0.2.12",
        "streamingServer": "https://192.0.2.12:12470",
    }
    fake.legacy = types.SimpleNamespace(
        get_json=lambda path, default: {
            "id": "org.stremiosrv.iptv.local", "version": "1.0.0"
        } if path.endswith("/manifest.json") else default,
    )
    monkeypatch.setitem(sys.modules, "version_lifecycle", fake)
    path = Path(__file__).resolve().parents[1] / "webadmin" / "addon_links.py"
    spec = importlib.util.spec_from_file_location("addon_links_testonly", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_iptv_addon_is_manageable_before_playlist_configuration(tmp_path, monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setenv("IPTV_CONFIG_FILE", str(tmp_path / "absent.json"))
    card = module._iptv_addon()
    assert card["id"] == "iptv-local"
    assert card["managementUrl"] == "/#iptv"
    assert card["manifestUrl"] == ""
    assert card["ready"] is False


def test_iptv_manifest_present_but_provider_credentials_not_exposed(tmp_path, monkeypatch):
    module = _module(monkeypatch)
    cfg = tmp_path / "iptv-local.json"
    cfg.write_text(json.dumps({
        "enabled": True,
        "playlist_source": "url",
        "playlist_url": "https://synthetic.example.net/live?login=FAKE&password=FAKE",
        "signing_secret": "a" * 64,
        "addon_token": "b" * 48,
    }))
    monkeypatch.setenv("IPTV_CONFIG_FILE", str(cfg))
    card = module._iptv_addon()
    assert card["ready"]
    assert card["manifestUrl"] == "https://192.0.2.12:12470/iptv/" + "b" * 48 + "/manifest.json"
    assert card["managementUrl"] == "/#iptv"
    exposed = json.dumps(card)
    assert "synthetic.example.net" not in exposed
    assert "FAKE" not in exposed
    assert "signing_secret" not in exposed


def test_iptv_manifest_after_local_m3u8_upload(tmp_path, monkeypatch):
    module = _module(monkeypatch)
    cfg = tmp_path / "iptv-local.json"
    upload = tmp_path / "iptv-playlist-upload.m3u"
    upload.write_text("#EXTM3U\\n#EXTINF:-1,Canal\\nhttps://synthetic.example.net/live\\n")
    monkeypatch.setenv("IPTV_UPLOAD_FILE", str(upload))
    cfg.write_text(json.dumps({
        "enabled": True, "playlist_source": "file", "playlist_url": "",
        "signing_secret": "a" * 64, "addon_token": "b" * 48,
    }))
    monkeypatch.setenv("IPTV_CONFIG_FILE", str(cfg))
    result = module._iptv_addon()
    assert result["ready"] is True
    assert result["manifestUrl"].endswith("/iptv/" + "b" * 48 + "/manifest.json")
    assert "synthetic.example.net" not in json.dumps(result)
