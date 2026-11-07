"""Regression tests for artwork on Library addon entries learned from playback."""
from stremiosrv.library import addon_model as am

IH = "0123456789abcdef0123456789abcdef01234567"


def _entry(label: dict) -> dict:
    return {
        "infoHash": IH,
        "name": "release.folder.name",
        "size": 1073741824,
        "state": "idle",
        "progress": 1.0,
        "pinned": False,
        "seeds": 0,
        "label": label,
        "files": [],
    }


def test_learned_imdb_label_gets_metahub_poster_in_catalog_preview():
    item = am.preview(_entry({"metaId": "tt1234567", "type": "movie"}))
    assert item["poster"] == "https://images.metahub.space/poster/medium/tt1234567/img"
    assert item["posterShape"] == "poster"


def test_learned_imdb_label_gets_metahub_poster_in_meta_detail():
    item = am.meta_for(_entry({"metaId": "tt1234567", "type": "movie"}))
    assert item["poster"] == "https://images.metahub.space/poster/medium/tt1234567/img"
    assert item["posterShape"] == "poster"


def test_explicit_label_poster_still_wins_over_fallback():
    explicit = "https://example.invalid/custom-poster.jpg"
    item = am.preview(_entry({"metaId": "tt1234567", "type": "movie", "poster": explicit}))
    assert item["poster"] == explicit


def test_unmatched_or_non_imdb_labels_do_not_invent_artwork():
    assert "poster" not in am.preview(_entry({"type": "other"}))
    assert "poster" not in am.preview(_entry({"metaId": "custom:123", "type": "other"}))
