"""HTTP-level checks of Stremio subtitle listing, selection and persistent reuse."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from stremiosrv.subtitle_translator import addon

IH = "a" * 40
VID = "tt1234567"
SRC = "https://subtitles.example.org/english.srt"
ORIGINAL = "1\n00:00:01,000 --> 00:00:02,000\nHello\n"
CHOICE = {
    "id": "e" * 64,
    "url": SRC,
    "sourceLanguage": "en",
    "name": "English original",
}


def _client(tmp_path, monkeypatch, calls):
    cache_dir = tmp_path / "main-title"
    cache_dir.mkdir()
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        cache_root=str(tmp_path),
        subtitle_source_addons="https://subtitles.example.org/manifest.json",
        library_addon_allow="",
    )
    app.state.engine = None
    app.include_router(addon.router)
    # This test verifies business flow, not private-network authentication.
    monkeypatch.setattr(addon, "_guard", lambda request, token: None)
    monkeypatch.setattr(addon, "_linked_hashes",
                        lambda root, video_id, request=None, type_="movie", extra="": [IH])
    monkeypatch.setattr(addon, "_title_present", lambda root, ih: ih == IH)
    monkeypatch.setattr(addon, "available_source_languages", lambda: {"en"})
    monkeypatch.setattr(addon, "_translation_preferences", lambda: ("en",))
    monkeypatch.setattr(
        addon.subtitle_sources,
        "discover",
        lambda config, media_type, video_id, extra="": [dict(CHOICE)],
    )
    def fetch(url, limit):
        calls["fetched"] += 1
        assert url == SRC
        return ORIGINAL.encode("utf-8")
    monkeypatch.setattr(addon.subtitle_sources, "fetch_bytes", fetch)
    from stremiosrv import subtitle_translator as core
    def translated(text, source, target="pt-PT"):
        calls["translated"] += 1
        assert source == "en"
        assert target == "pt-PT"
        return "Olá"
    monkeypatch.setattr(core, "offline_argos", translated)
    return TestClient(app)


def test_select_translate_and_second_play_uses_persistent_cache(tmp_path, monkeypatch):
    calls = {"fetched": 0, "translated": 0}
    client = _client(tmp_path, monkeypatch, calls)
    first = client.get(f"/subtitle-translator/tok/subtitles/movie/{VID}.json")
    assert first.status_code == 200
    choices = first.json()["subtitles"]
    assert len(choices) == 1
    assert choices[0]["lang"] == "pt-PT"
    assert "EN → PT" in choices[0]["name"]
    path = urlsplit(choices[0]["url"]).path
    query = urlsplit(choices[0]["url"]).query
    response = client.get(path + "?" + query)
    assert response.status_code == 200, response.text
    assert "Olá" in response.text
    assert "00:00:01,000 --> 00:00:02,000" in response.text
    assert calls == {"fetched": 1, "translated": 1}

    second_listing = client.get(f"/subtitle-translator/tok/subtitles/movie/{VID}.json")
    assert second_listing.status_code == 200
    cached_tracks = second_listing.json()["subtitles"]
    assert len(cached_tracks) == 1
    assert "Em cache" in cached_tracks[0]["name"]
    existing_url = urlsplit(cached_tracks[0]["url"]).path
    replay = client.get(existing_url)
    assert replay.status_code == 200
    assert replay.content == response.content
    assert calls == {"fetched": 1, "translated": 1}

    assert list((Path(tmp_path) / ".subtitle-translator" / IH).glob("*.srt"))


def test_manual_uploader_is_disabled_outside_private_qa(tmp_path, monkeypatch):
    monkeypatch.delenv("STREMIOSRV_SUBTITLE_TRANSLATOR_QA", raising=False)
    client = _client(tmp_path, monkeypatch, {"fetched": 0, "translated": 0})
    assert client.get("/subtitle-translator/tok/manage").status_code == 404
    assert client.get("/subtitle-translator/tok/titles.json").status_code == 404
    assert client.post("/subtitle-translator/tok/translate", json={
        "infoHash": IH, "videoId": VID, "sourceLanguage": "en",
        "text": ORIGINAL,
    }).status_code == 404


def test_source_without_local_model_is_not_advertised(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, {"fetched": 0, "translated": 0})
    monkeypatch.setattr(addon, "available_source_languages", lambda: set())
    result = client.get(f"/subtitle-translator/tok/subtitles/movie/{VID}.json")
    assert result.status_code == 200
    assert result.json()["subtitles"] == []
