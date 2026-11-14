"""Fresh snapshot must restore five model paths without bundled binary data."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _resolver():
    spec = importlib.util.spec_from_file_location(
        "subtitle_model_root", ROOT / "scripts/resolve-subtitle-model-dir.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_root_from_env_preserves_relative_and_absolute_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("STREMIOSRV_SUBTITLE_MODELS_DIR", raising=False)
    settings = tmp_path / ".env"
    settings.write_text("STREMIOSRV_SUBTITLE_MODELS_DIR=./models/subtitles\n")
    assert _resolver().resolve(settings, tmp_path) == tmp_path / "models/subtitles"
    settings.write_text("STREMIOSRV_SUBTITLE_MODELS_DIR=/srv/independent-models\n")
    assert _resolver().resolve(settings, tmp_path) == Path("/srv/independent-models")


def test_duplicate_root_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("STREMIOSRV_SUBTITLE_MODELS_DIR", raising=False)
    settings = tmp_path / ".env"
    settings.write_text("STREMIOSRV_SUBTITLE_MODELS_DIR=/one\nSTREMIOSRV_SUBTITLE_MODELS_DIR=/two\n")
    with pytest.raises(ValueError, match="duplicate"):
        _resolver().resolve(settings, tmp_path)


def test_injected_root_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("STREMIOSRV_SUBTITLE_MODELS_DIR", raising=False)
    settings = tmp_path / ".env"
    settings.write_text("STREMIOSRV_SUBTITLE_MODELS_DIR=$(touch /tmp/unsafe)\n")
    with pytest.raises(ValueError, match="invalid"):
        _resolver().resolve(settings, tmp_path)


def test_five_models_and_checksum_are_pinned():
    script = (ROOT / "scripts/provision-subtitle-models.sh").read_text()
    pairs = ("en_pt", "fr_en", "es_pt", "ru_en", "cs_en")
    for pair in pairs:
        assert f"|{pair}|" in script
    assert "e9ba8bf722d10a4a4c39f74289d5938fd47eac08dbe4ed0afd22d89445a5c3ac" in script
    assert "f1683b1ac5e826c7e3db2b0ea5d73191cdd695d6523b112363d8a769203dcb62" in script
    assert "sha256sum -c -" in script
    assert "--download-and-install" in script


def test_clean_setup_provisions_before_core_start():
    script = (ROOT / "packaging/stremio-webadmin-setup.sh").read_text()
    assert script.index("provision-subtitle-models.sh") < script.index("sh ./start.sh")
    assert "STREMIO_PROVISION_SUBTITLE_MODELS:-1" in script
    assert "resolve-subtitle-model-dir.py" in script
    assert "curl:curl" in (ROOT / "packaging/stremio-webadmin-prerequisites.sh").read_text()
