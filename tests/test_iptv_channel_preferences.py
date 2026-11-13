"""Standalone IPTV channel-order and deletion regression tests.

The fixtures contain synthetic URLs only; no provider M3U/M3U8 files,
credentials, tokens or playlists from any installed server are included.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from stremiosrv.iptv import store


M3U = """#EXTM3U
#EXTINF:-1 tvg-id="z" tvg-chno="30" group-title="Noticias",Zeta
https://media.example.net/30.m3u8
#EXTINF:-1 tvg-id="b" tvg-chno="2" group-title="Noticias",Beta
https://media.example.net/2.m3u8
#EXTINF:-1 tvg-id="a" group-title="Geral",100 | Alfa
https://media.example.net/100.m3u8
#EXTINF:-1 tvg-id="c" group-title="Geral",Canal sem número
https://media.example.net/other.m3u8
"""

HLS_MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
https://media.example.net/live/720p.m3u8
"""

HLS_MEDIA = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXTINF:6,
https://media.example.net/live/segment000.ts
"""


def _names(rows):
    return [item.name for item in rows.values()]


def test_m3u_and_m3u8_channel_sources_preserve_original_numbering():
    # Extension belongs to URL; channel-list recognition is based on content.
    parsed = store.parse_m3u(M3U)
    assert len(parsed) == 4
    ordered = store.sorted_channels(parsed, {"excluded": [], "order": {}})
    assert [channel.name for channel in ordered] == [
        "Beta", "Zeta", "100 | Alfa", "Canal sem número"
    ]
    assert ordered[0].number == 2
    assert ordered[1].number == 30
    assert ordered[2].number == 100
    assert [channel.position for channel in parsed.values()] == [1, 2, 3, 4]


@pytest.mark.parametrize("manifest", [HLS_MASTER, HLS_MEDIA])
def test_hls_segment_or_master_playlist_is_not_imported_as_channel_list(manifest):
    with pytest.raises(ValueError, match="HLS"):
        store.parse_m3u(manifest)


def test_channel_identity_does_not_change_when_number_changes():
    original = store.parse_m3u(M3U)
    changed = store.parse_m3u(M3U.replace('tvg-chno="30"', 'tvg-chno="300"'))
    assert list(original) == list(changed)


def test_persistent_hide_and_manual_order_without_changing_m3u(tmp_path, monkeypatch):
    config = tmp_path / "iptv-local.json"
    config.write_text(json.dumps({
        "playlist_url": "https://synthetic.example.net/live/playlist.m3u8",
        "addon_token": "a" * 48,
        "signing_secret": "b" * 64,
        "enabled": True,
    }))
    config.chmod(0o600)
    prefs = tmp_path / "iptv-channel-preferences.json"
    monkeypatch.setenv("STREMIOSRV_IPTV_CONFIG", str(config))
    monkeypatch.setenv("STREMIOSRV_IPTV_PREFERENCES", str(prefs))
    monkeypatch.setattr(store, "_download", lambda _: M3U)
    store.reset_cache()
    before = store.channels(force=True)
    assert len(before) == 4
    beta_id = next(ch.id for ch in before.values() if ch.name == "Beta")
    zeta_id = next(ch.id for ch in before.values() if ch.name == "Zeta")

    prefs.write_text(json.dumps({
        "excluded": [beta_id], "order": {zeta_id: 1}
    }))
    after = store.channels()
    assert len(after) == 3
    assert beta_id not in after
    assert _names(after)[0] == "Zeta"

    store.reset_cache()
    refreshed = store.channels(force=True)
    assert len(refreshed) == 3
    assert beta_id not in refreshed
    assert _names(refreshed)[0] == "Zeta"
    assert store.resolve_job(store.signing_job(beta_id)) is None
    assert len(store.parse_m3u(M3U)) == 4
    store.reset_cache()


def test_preferences_reject_invalid_identifiers(tmp_path, monkeypatch):
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({
        "excluded": ["../../private", "a" * 24],
        "order": {"b" * 24: 14, "../x": 1, "c" * 24: -2},
    }))
    monkeypatch.setenv("STREMIOSRV_IPTV_PREFERENCES", str(path))
    data = store.preferences()
    assert data["excluded"] == ["a" * 24]
    assert data["order"] == {"b" * 24: 14}


def test_private_uploaded_m3u8_is_loaded_locally_without_network(tmp_path, monkeypatch):
    path = tmp_path / "iptv-local.json"
    upload = tmp_path / "iptv-playlist-upload.m3u"
    upload.write_text(M3U, encoding="utf-8")
    upload.chmod(0o600)
    path.write_text(json.dumps({
        "playlist_url": "", "playlist_source": "file",
        "addon_token": "a" * 48, "signing_secret": "b" * 64,
        "enabled": True,
    }))
    path.chmod(0o600)
    monkeypatch.setenv("STREMIOSRV_IPTV_CONFIG", str(path))
    monkeypatch.setenv("STREMIOSRV_IPTV_UPLOAD", str(upload))
    monkeypatch.setenv("STREMIOSRV_IPTV_PREFERENCES", str(tmp_path / "prefs.json"))
    def no_network(*args):
        raise AssertionError("file upload must not trigger provider HTTP request")
    monkeypatch.setattr(store, "_download", no_network)
    store.reset_cache()
    result = store.channels(force=True)
    assert len(result) == 4
    assert _names(result)[0] == "Beta"
    assert store.enabled()
    store.reset_cache()


def test_uploaded_m3u8_with_hls_only_is_rejected_by_core(tmp_path, monkeypatch):
    config = tmp_path / "iptv-local.json"
    upload = tmp_path / "iptv-playlist-upload.m3u"
    upload.write_text(HLS_MASTER, encoding="utf-8")
    upload.chmod(0o600)
    config.write_text(json.dumps({
        "playlist_url": "", "playlist_source": "file",
        "addon_token": "a" * 48, "signing_secret": "b" * 64,
    }))
    config.chmod(0o600)
    monkeypatch.setenv("STREMIOSRV_IPTV_CONFIG", str(config))
    monkeypatch.setenv("STREMIOSRV_IPTV_UPLOAD", str(upload))
    store.reset_cache()
    with pytest.raises(ValueError, match="HLS"):
        store.channels(force=True)
    store.reset_cache()


def test_metadata_ext_x_session_data_does_not_turn_iptv_catalogue_into_hls():
    playlist = (
        '#EXTM3U\n'
        '#EXT-X-SESSION-DATA:DATA-ID="com.example.demo",VALUE="synthetic"\n'
        '#EXTINF:-1 tvg-chno="23",Canal Exemplo\n'
        'https://synthetic.example.net/channel/23.m3u8\n'
    )
    assert not store.is_hls_manifest(playlist)
    channels = store.parse_m3u(playlist)
    assert len(channels) == 1
    assert next(iter(channels.values())).number == 23


def test_metadata_382_channels_is_not_hls():
    playlist = '#EXTM3U\n#EXT-X-SESSION-DATA:DATA-ID="com.example.test",VALUE="demo"\n'
    playlist += ''.join(
        f'#EXTINF:-1 tvg-chno="{i}",Example Channel {i}\n'
        f'https://synthetic.example.net/stream/{i}.m3u8\n'
        for i in range(1, 383)
    )
    assert len(store.parse_m3u(playlist)) == 382
    assert not store.is_hls_manifest(playlist)
