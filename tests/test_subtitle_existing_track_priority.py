"""Subtitle translator: existing source tracks, priority and HTTPS/own-cache identity."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from stremiosrv.subtitle_translator import addon
from stremiosrv.subtitle_translator import local_engine


IH = "a" * 40


def _fake_model(root: Path, pair: str) -> None:
    for name in ("model/model.bin", "model/config.json", "sentencepiece.model"):
        p = root / pair / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake")


def test_offline_translation_paths_direct_and_french_pivot(tmp_path):
    _fake_model(tmp_path, "es_pt")
    _fake_model(tmp_path, "en_pt")
    _fake_model(tmp_path, "fr_en")
    assert local_engine.translation_path("en", root=tmp_path) == (("en", "pt"),)
    assert local_engine.translation_path("es", root=tmp_path) == (("es", "pt"),)
    assert local_engine.translation_path("fr", root=tmp_path) == (("fr", "en"), ("en", "pt"))
    assert local_engine.supported_source_languages(root=tmp_path) == {"en", "fr", "es"}
    assert local_engine.translation_path("de", root=tmp_path) == ()
    _fake_model(tmp_path, "fr_pt")
    assert local_engine.translation_path("fr", root=tmp_path) == (("fr", "pt"),)


def test_subtitle_choices_from_existing_provider_sorted_en_fr_es(monkeypatch):
    monkeypatch.setattr(addon, '_translation_preferences', lambda: ('en','fr','es'))
    options = [
        {"id": "s", "sourceLanguage": "es", "name": "spanish"},
        {"id": "f", "sourceLanguage": "fr", "name": "french"},
        {"id": "e", "sourceLanguage": "en", "name": "english"},
    ]
    assert [x["id"] for x in addon._ranked_options(options)] == ["e", "f", "s"]


def test_canonical_my_library_id_links_to_exact_cache_without_imdb(monkeypatch):
    monkeypatch.setattr(addon, "_title_present", lambda root, digest: digest == IH)
    h = SimpleNamespace(has_metadata=lambda: True, num_files=lambda: 1)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        engine=SimpleNamespace(get=lambda hash_: h if hash_ == IH else None)
    )))
    assert addon._linked_hashes("/none", f"stremiosrv:{IH}") == [IH]
    assert addon._linked_hashes("/none", f"stremiosrv:{IH}:0", request) == [IH]
    assert addon._linked_hashes("/none", f"stremiosrv:{IH}:9", request) == []
    assert addon._linked_hashes("/none", "stremiosrv:" + "b" * 40, request) == []


def test_existing_local_subtitle_file_is_advertised_but_other_title_is_not(tmp_path, monkeypatch):
    title = "My.Movie.2026.mkv"
    (tmp_path / title).write_bytes(b"synthetic movie")
    (tmp_path / "My.Movie.2026.en.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf8"
    )
    (tmp_path / "Other.Movie.fr.srt").write_text("not the same movie")
    monkeypatch.setattr(addon.cache, "load_name_index", lambda root: {title: IH})
    results = addon._existing_local_tracks(str(tmp_path), IH, f"stremiosrv:{IH}")
    assert len(results) == 1
    assert results[0]["sourceLanguage"] == "en"
    assert results[0]["kind"] == "local"
    assert results[0]["path"].name == "My.Movie.2026.en.srt"
    assert addon._existing_local_tracks(str(tmp_path), "b" * 40, "tt12345678") == []


def test_reject_symlinked_local_source(tmp_path, monkeypatch):
    title = "My.Movie.mkv"
    (tmp_path / title).write_bytes(b"synthetic")
    elsewhere = tmp_path / "elsewhere.srt"
    elsewhere.write_text("not trusted")
    (tmp_path / "My.Movie.en.srt").symlink_to(elsewhere)
    monkeypatch.setattr(addon.cache, "load_name_index", lambda root: {title: IH})
    assert addon._existing_local_tracks(str(tmp_path), IH, "tt12345678") == []


def test_streaming_subtitle_urls_must_be_pinned_to_trusted_https(monkeypatch):
    monkeypatch.setenv("STREMIOSRV_SUBTITLE_PUBLIC_ORIGIN", "https://stream.example.test:12470")
    req = SimpleNamespace(client=SimpleNamespace(host="172.20.0.5"),
                          headers={"host": "internal:11470"}, url=SimpleNamespace(
                              scheme="http", netloc="internal:11470"))
    assert addon._origin(req) == "https://stream.example.test:12470"
    monkeypatch.setenv("STREMIOSRV_SUBTITLE_PUBLIC_ORIGIN", "http://internal:11470")
    with pytest.raises(HTTPException) as error:
        addon._origin(req)
    assert error.value.status_code == 503
