from stremiosrv.library import addon_model
from stremiosrv.library import metadata
import stremiosrv.library as library_pkg


IH = "a1b2c3d4e5" * 4


def test_release_hint_for_episode_release():
    hint = metadata.release_hint(
        "The.Gentlemen.S02E04.The.Bigger.Picture.1080p.HEVC.x265-MeGusta[EZTVx.to].mkv"
    )
    assert hint == {
        "title": "The Gentlemen",
        "type": "series",
        "season": 2,
        "episode": 4,
    }


def test_release_hint_for_show_year_before_season():
    hint = metadata.release_hint("The.Gentlemen.2024.S02E02.1080p.HEVC.x265.mkv")
    assert hint["title"] == "The Gentlemen"
    assert hint["type"] == "series"
    assert hint["year"] == 2024
    assert hint["season"] == 2
    assert hint["episode"] == 2


def test_release_hint_does_not_treat_season_release_year_as_show_year():
    hint = metadata.release_hint("The Gentlemen S02 (2026) 1080p WEBRip x265 DDP 5.1 ESub")
    assert hint["title"] == "The Gentlemen"
    assert hint["type"] == "series"
    assert hint["season"] == 2
    assert "year" not in hint


def test_numeric_series_title_is_not_used_as_year_constraint():
    hint = metadata.release_hint("1923.S02E01.1080p.WEB-DL.mkv")
    assert hint["title"] == "1923"
    assert hint["type"] == "series"
    assert hint["season"] == 2
    assert hint["episode"] == 1
    assert "year" not in hint


def test_resolve_accepts_one_exact_cinemeta_match(monkeypatch):
    monkeypatch.setattr(metadata, "_search", lambda type_, title: ({
        "id": "tt13210838",
        "name": "The Gentlemen",
        "releaseInfo": "2024–",
        "poster": "https://example.invalid/poster.jpg",
    },))
    label = metadata.resolve("The.Gentlemen.2024.S02E04.1080p.WEB-DL.mkv")
    assert label == {
        "metaId": "tt13210838",
        "type": "series",
        "name": "The Gentlemen",
        "poster": "https://example.invalid/poster.jpg",
        "season": 2,
        "episode": 4,
        "videoId": "tt13210838:2:4",
    }


def test_resolve_rejects_ambiguous_exact_matches(monkeypatch):
    monkeypatch.setattr(metadata, "_search", lambda type_, title: (
        {"id": "tt0000001", "name": "Same Name"},
        {"id": "tt0000002", "name": "Same Name"},
    ))
    assert metadata.resolve("Same.Name.1080p.WEBRip.mkv") is None


def test_unlabelled_catalog_entry_gets_display_only_name_and_poster(monkeypatch):
    monkeypatch.setattr(library_pkg._metadata, "resolve", lambda name: {
        "metaId": "tt13210838",
        "type": "series",
        "name": "The Gentlemen",
        "poster": "https://example.invalid/poster.jpg",
        "season": 2,
        "episode": 4,
    })
    entry = {
        "infoHash": IH,
        "name": "The.Gentlemen.S02E04.1080p.HEVC.mkv",
        "size": 700_000_000,
        "state": "idle",
        "progress": 1.0,
        "pinned": False,
        "seeds": 0,
        "label": None,
    }
    item = addon_model.preview(entry)
    assert item["name"] == "The Gentlemen · S02E04"
    assert item["poster"] == "https://example.invalid/poster.jpg"
    assert item["posterShape"] == "poster"
    # Recognition is presentation-only; it must not mutate the authoritative state entry.
    assert entry["label"] is None


def test_existing_learned_identity_cannot_be_replaced_by_filename_guess(monkeypatch):
    monkeypatch.setattr(library_pkg._metadata, "resolve", lambda name: {
        "metaId": "tt9999999",
        "type": "series",
        "name": "Wrong",
        "poster": "https://example.invalid/wrong.jpg",
    })
    entry = {
        "infoHash": IH,
        "name": "Some.Release.S01E01.mkv",
        "size": 1,
        "state": "idle",
        "progress": 1.0,
        "pinned": False,
        "seeds": 0,
        "label": {"metaId": "tt1111111", "type": "series", "season": 1, "episode": 1},
    }
    item = addon_model.preview(entry)
    assert item["poster"].endswith("/tt1111111/img")
    assert "Wrong" not in item["name"]
