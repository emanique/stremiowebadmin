"""End-to-end routing of an existing sidecar subtitle to a lazy translated SRT.

Uses a synthetic media cache; no access to a real title or provider account.
"""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from stremiosrv.subtitle_translator import addon
from stremiosrv.subtitle_translator import local_engine


def test_existing_subtitle_selected_once_then_reused(tmp_path, monkeypatch):
    ih = "b" * 40
    media = tmp_path / "Example.Film.mkv"
    media.write_bytes(b"synthetic placeholder")
    (tmp_path / "Example.Film.en.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello my friend.\n\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(addon.cache, "load_name_index",
                        lambda root: {media.name: ih})
    monkeypatch.setattr(addon, "_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(addon, "_origin", lambda request: "https://stream.example.test")
    monkeypatch.setattr(addon.subtitle_sources, "configured_sources", lambda _: "")
    monkeypatch.setattr(addon, "available_source_languages", lambda: {"en"})
    monkeypatch.setattr(addon, "_translation_preferences", lambda: ("en",))
    monkeypatch.setattr(local_engine, "translation_path", lambda *args, **kwargs: (("en", "pt"),))
    calls = []
    def translate(texts, source, target="pt-PT"):
        calls.append((texts, source, target))
        return ["Olá, meu amigo." for _ in texts]
    monkeypatch.setattr(local_engine, "translate_many", translate)
    app = FastAPI()
    app.state.settings = SimpleNamespace(cache_root=str(tmp_path))
    app.include_router(addon.router)
    client = TestClient(app)
    video_id = "stremiosrv:" + ih
    response = client.get("/subtitle-translator/test/subtitles/other/" + video_id + ".json")
    assert response.status_code == 200, response.text
    options = response.json()["subtitles"]
    assert len(options) == 1
    assert options[0]["lang"] == "pt-PT"
    assert options[0]["url"].startswith("https://stream.example.test/")
    assert "EN → PT" in options[0]["name"]
    chosen = urlsplit(options[0]["url"])
    first = client.get(chosen.path + ("?" + chosen.query if chosen.query else ""))
    assert first.status_code == 200, first.text
    assert "Olá, meu amigo." in first.text
    assert "00:00:01,000 --> 00:00:02,000" in first.text
    second = client.get(chosen.path + ("?" + chosen.query if chosen.query else ""))
    assert second.status_code == 200 and first.text == second.text
    assert len(calls) == 1
    cached = client.get("/subtitle-translator/test/subtitles/other/" + video_id + ".json")
    assert any("Em cache" in x["name"] for x in cached.json()["subtitles"])
