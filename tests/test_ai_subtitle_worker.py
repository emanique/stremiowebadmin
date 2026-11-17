"""Verifies that the AI worker only starts after libtorrent file completion."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from stremiosrv.ai_subtitles.service import AutoSubtitleWorker


def _fake_handle(hash_value: str, complete: bool):
    handle = Mock()
    handle.info_hash.return_value = hash_value
    handle.has_metadata.return_value = True
    handle.file_paths.return_value = ["film.mkv"]
    handle.file_complete.return_value = complete
    return handle


def test_worker_ignores_incomplete_torrent(tmp_path, monkeypatch):
    (tmp_path / "film.mkv").write_bytes(b"video")
    model = tmp_path / "ggml-small.bin"
    model.write_bytes(b"model")
    monkeypatch.setenv("STREMIOSRV_AI_WHISPER_MODEL", str(model))
    engine = Mock()
    engine.active.return_value = [_fake_handle("a" * 40, False)]
    worker = AutoSubtitleWorker(engine, str(tmp_path))
    with patch("stremiosrv.ai_subtitles.service.generate") as generate:
        assert worker.scan_once() is False
        generate.assert_not_called()


def test_worker_generates_complete_torrent_once(tmp_path, monkeypatch):
    (tmp_path / "film.mkv").write_bytes(b"video")
    model = tmp_path / "ggml-small.bin"
    model.write_bytes(b"model")
    monkeypatch.setenv("STREMIOSRV_AI_WHISPER_MODEL", str(model))
    engine = Mock()
    engine.active.return_value = [_fake_handle("b" * 40, True)]
    worker = AutoSubtitleWorker(engine, str(tmp_path))
    with patch("stremiosrv.ai_subtitles.service.generate") as generate:
        assert worker.scan_once() is True
        args, kwargs = generate.call_args
        assert args[2] == "b" * 40 and args[3] == 0
        assert kwargs["verified_complete"] is True


def test_worker_throttles_failed_inference(tmp_path, monkeypatch):
    from stremiosrv.ai_subtitles.generator import GenerationUnavailable
    (tmp_path / "film.mkv").write_bytes(b"video")
    model = tmp_path / "ggml-small.bin"
    model.write_bytes(b"model")
    monkeypatch.setenv("STREMIOSRV_AI_WHISPER_MODEL", str(model))
    engine = Mock()
    engine.active.return_value = [_fake_handle("c" * 40, True)]
    worker = AutoSubtitleWorker(engine, str(tmp_path))
    with patch("stremiosrv.ai_subtitles.service.generate",
               side_effect=GenerationUnavailable("unsupported language")) as generate:
        assert worker.scan_once() is True
        assert worker.scan_once() is False
        assert generate.call_count == 1
