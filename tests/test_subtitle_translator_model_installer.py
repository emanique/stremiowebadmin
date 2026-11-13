"""Model package installer safety checks, without downloading dependencies."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


def _installer():
    path = Path(__file__).resolve().parents[1] / "scripts" / "subtitle-model-install.py"
    spec = importlib.util.spec_from_file_location("subtitle_model_install", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundle(tmp_path):
    path = tmp_path / "model.argosmodel"
    with zipfile.ZipFile(path, "w") as file:
        prefix = "translate-es_pt-1_0/"
        file.writestr(prefix + "metadata.json",
                      json.dumps({"from_code": "es", "to_code": "pt"}))
        file.writestr(prefix + "sentencepiece.model", b"unit-model")
        file.writestr(prefix + "model/config.json", "{}")
        file.writestr(prefix + "model/model.bin", b"unit-model")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_verified_offline_install(tmp_path):
    archive, sha = _bundle(tmp_path)
    result = _installer().install(archive, tmp_path / "models", sha)
    assert result.name == "es_pt"
    assert (result / "model/model.bin").read_bytes() == b"unit-model"
    assert (result / "INSTALL.json").is_file()


def test_checksum_mismatch_rejected(tmp_path):
    archive, _ = _bundle(tmp_path)
    with pytest.raises(ValueError, match="mismatch"):
        _installer().install(archive, tmp_path / "models", "0" * 64)


def test_archive_install_never_overwrites_existing_model(tmp_path):
    archive, sha = _bundle(tmp_path)
    _installer().install(archive, tmp_path / "models", sha)
    with pytest.raises(FileExistsError):
        _installer().install(archive, tmp_path / "models", sha)


def test_model_package_rejects_missing_metadata(tmp_path):
    archive = tmp_path / "broken.argosmodel"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("test/model/model.bin", b"oops")
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="metadata"):
        _installer().install(archive, tmp_path / "models", sha)
