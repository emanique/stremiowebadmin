"""Pure tests for GTSubs-style source discovery without Internet."""
import json
import pytest

from stremiosrv.subtitle_translator import sources


def test_manifest_configuration_is_explicit_and_https():
    assert sources.provider_roots("") == ()
    assert sources.provider_roots("https://subs.example.org/manifest.json") == (
        "https://subs.example.org",
    )
    with pytest.raises(ValueError):
        sources.provider_roots("http://localhost:8080/manifest.json")
    with pytest.raises(ValueError):
        sources.provider_roots("https://foo.example.org/manifest.json?token=secret")
    with pytest.raises(ValueError):
        sources.provider_roots("https://user:pass@foo.example.org/manifest.json")


def test_discovery_exposes_language_choices_without_translation(monkeypatch):
    calls = []
    def fetch(url, limit):
        calls.append(url)
        return json.dumps({"subtitles": [
            {"url": "https://cdn.example.org/original.srt",
             "lang": "en", "name": "English source"},
            {"url": "https://cdn.example.org/portuguese.srt", "lang": "pt"},
            {"url": "http://internal.invalid/private.srt", "lang": "fr"},
        ]}).encode()
    monkeypatch.setattr(sources, "fetch_bytes", fetch)
    items = sources.discover(
        "https://subs.example.org/manifest.json", "movie", "tt1234567"
    )
    assert len(items) == 1
    assert items[0]["sourceLanguage"] == "en"
    assert len(items[0]["id"]) == 64
    assert calls == ["https://subs.example.org/subtitles/movie/tt1234567.json"]


def test_selected_track_key_is_stable_and_different_for_sources():
    first = sources.source_id("https://cdn.example.org/a.srt", "en", "tt1234567")
    assert first == sources.source_id("https://cdn.example.org/a.srt", "en", "tt1234567")
    assert first != sources.source_id("https://cdn.example.org/b.srt", "en", "tt1234567")


def test_invalid_video_is_not_queried(monkeypatch):
    def fail(*args):
        raise AssertionError("network should never be accessed")
    monkeypatch.setattr(sources, "fetch_bytes", fail)
    assert sources.discover("https://subs.example.org/manifest.json", "movie", "../../") == []


def test_webadmin_config_persisted_sources_are_read_without_restart(tmp_path, monkeypatch):
    from types import SimpleNamespace
    config_path = tmp_path / "admin-settings.json"
    monkeypatch.setenv("STREMIOSRV_EXTERNAL_CONFIG", str(config_path))
    config_path.write_text(json.dumps({
        "subtitle_source_addons": "https://first.example.org/manifest.json"
    }))
    settings = SimpleNamespace(subtitle_source_addons="")
    assert sources.configured_sources(settings).startswith("https://first.")
    config_path.write_text(json.dumps({
        "subtitle_source_addons": "https://second.example.org/manifest.json"
    }))
    assert sources.configured_sources(settings).startswith("https://second.")


def test_webadmin_config_falls_back_to_runtime_for_invalid_json(tmp_path, monkeypatch):
    from types import SimpleNamespace
    config_path = tmp_path / "admin-settings.json"
    config_path.write_text("{not-json")
    monkeypatch.setenv("STREMIOSRV_EXTERNAL_CONFIG", str(config_path))
    settings = SimpleNamespace(subtitle_source_addons="https://fallback.example/manifest.json")
    assert sources.configured_sources(settings) == settings.subtitle_source_addons


def test_iso639_2_language_codes_from_official_provider():
    assert sources.normalize_language("ger") == "de"
    assert sources.normalize_language("eng") == "en"
    assert sources.normalize_language("spa") == "es"
    assert sources.normalize_language("por") == "pt"
    assert sources.normalize_language("ell") == "el"
    assert sources.normalize_language("nonsense") is None


def test_discovery_official_three_letter_language(monkeypatch):
    def fetch(url, limit):
        return json.dumps({"subtitles": [
            {"url": "https://subs5.strem.io/de/example", "lang": "ger", "name": "German"},
            {"url": "https://subs5.strem.io/pt/example", "lang": "por", "name": "Portuguese"},
        ]}).encode("utf-8")
    monkeypatch.setattr(sources, "fetch_bytes", fetch)
    found = sources.discover("https://opensubtitles-v3.strem.io/manifest.json", "movie", "tt0133093")
    assert len(found) == 1
    assert found[0]["sourceLanguage"] == "de"
