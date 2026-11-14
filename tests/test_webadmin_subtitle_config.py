"""Subtitle Translator WebAdmin endpoint regression and persistence contract."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webadmin"))
import iptv_management as web  # noqa: E402
import app as legacy  # noqa: E402


@pytest.fixture
def configured(tmp_path, monkeypatch):
    models = tmp_path / "models"
    for pair in ("en_pt", "fr_en", "es_pt", "ru_en"):
        root = models / pair
        (root / "model").mkdir(parents=True)
        for name in ("model/model.bin", "model/config.json", "sentencepiece.model"):
            (root / name).write_bytes(b"test")
    prefs = tmp_path / "config" / "subtitle-translator-settings.json"
    monkeypatch.setattr(legacy, "_SUBTITLE_MODEL_ROOT", models)
    monkeypatch.setattr(legacy, "_SUBTITLE_SETTINGS_FILE", prefs)
    monkeypatch.setattr(legacy, "audit", lambda *args: None)
    return TestClient(web.app), prefs


def test_get_lists_four_installed_languages(configured):
    client, _ = configured
    r = client.get("/api/subtitle-translator/config")
    assert r.status_code == 200
    p = r.json()
    assert p["languages"] == ["en", "fr", "es", "ru"]
    assert [x["code"] for x in p["available"]] == p["languages"]
    assert p["available"][3]["route"] == "ru → en → pt-PT"


def test_put_persists_reordered_selection_without_restart(configured):
    client, prefs = configured
    result = client.put("/api/subtitle-translator/config",
                        json={"languages": ["ru", "en"]})
    assert result.status_code == 200
    assert result.json()["languages"] == ["ru", "en"]
    assert json.loads(prefs.read_text()) == {"languages": ["ru", "en"]}
    assert client.get("/api/subtitle-translator/config").json()["languages"] == ["ru", "en"]


def test_put_rejects_unavailable_and_duplicate_languages(configured):
    client, prefs = configured
    for codes in (["ru", "ru"], ["de"], ["en", "../unsafe"]):
        assert client.put("/api/subtitle-translator/config",
                          json={"languages": codes}).status_code == 422
    assert not prefs.exists()


def test_empty_selection_allowed(configured):
    client, _ = configured
    assert client.put("/api/subtitle-translator/config",
                      json={"languages": []}).json()["languages"] == []


def test_symlink_not_overwritten(configured, tmp_path):
    client, prefs = configured
    prefs.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "preserve"
    target.write_text("leave alone")
    prefs.symlink_to(target)
    assert client.put("/api/subtitle-translator/config",
                      json={"languages": ["en"]}).status_code == 409
    assert target.read_text() == "leave alone"


def test_corrupt_preferences_fallback_to_installed(configured):
    client, prefs = configured
    prefs.parent.mkdir(parents=True, exist_ok=True)
    prefs.write_text("[]")
    assert client.get("/api/subtitle-translator/config").json()["languages"] == ["en", "fr", "es", "ru"]

def test_czech_bpe_model_is_advertised(tmp_path, monkeypatch):
    root = tmp_path / "models"
    for pair, token in (("en_pt", "sentencepiece.model"), ("cs_en", "bpe.model")):
        base = root / pair
        (base / "model").mkdir(parents=True)
        for rel in ("model/model.bin", "model/config.json", token):
            (base / rel).write_bytes(b"qa")
    monkeypatch.setattr(legacy, "_SUBTITLE_MODEL_ROOT", root)
    monkeypatch.setattr(legacy, "_SUBTITLE_SETTINGS_FILE", tmp_path / "fresh-config.json")
    result = TestClient(web.app).get("/api/subtitle-translator/config")
    assert result.status_code == 200
    assert [item["code"] for item in result.json()["available"]] == ["en", "cs"]
    assert "cs → en → pt-PT" in result.json()["available"][1]["route"]
