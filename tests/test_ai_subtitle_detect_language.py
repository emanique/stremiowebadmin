"""Prevent silent intros from forcing a wrong Whisper language."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from stremiosrv.ai_subtitles import generator as gen


def fake_output(language: str) -> dict:
    return {
        "result": {"language": language},
        "transcription": [{
            "offsets": {"from": 0, "to": 1700},
            "text": "Hello, please confirm the audio.",
        }],
    }


def test_dialogue_sample_selects_installed_language(tmp_path, monkeypatch):
    media = tmp_path / "film.mkv"
    media.write_bytes(b"video")
    probes = []

    def fake_run(args, **kwargs):
        probes.append(args)
        if args[0] == "ffprobe":
            return SimpleNamespace(stdout="6960\n", returncode=0)
        assert "-ss" in args and args[args.index("-ss") + 1] == "720"
        Path(args[-1]).write_bytes(b"wav")
        return SimpleNamespace(returncode=0)

    def fake_bridge(path, endpoint, token, *args, **kwargs):
        (path / "whisper.json").write_text(json.dumps(fake_output("en")))

    monkeypatch.setattr(gen.subprocess, "run", fake_run)
    monkeypatch.setattr(gen, "run_openvino_bridge", fake_bridge)
    with patch("stremiosrv.subtitle_translator.local_engine.translation_path",
               return_value=(("en", "pt"),)):
        language = gen.detect_speech_language(media, "http://ai-openvino:19098",
                                               "a" * 64, str(tmp_path))
    assert language == "en"
    assert probes[0][0] == "ffprobe"


def test_unsupported_language_is_rejected_before_full_inference(tmp_path, monkeypatch):
    media = tmp_path / "film.mkv"
    media.write_bytes(b"video")

    def fake_run(args, **kwargs):
        if args[0] == "ffprobe":
            return SimpleNamespace(stdout="6960\n", returncode=0)
        Path(args[-1]).write_bytes(b"wav")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(gen.subprocess, "run", fake_run)
    monkeypatch.setattr(gen, "run_openvino_bridge",
                        lambda path, *a, **k: (path / "whisper.json").write_text(
                            json.dumps(fake_output("la"))))
    with patch("stremiosrv.subtitle_translator.local_engine.translation_path",
               return_value=()):
        with pytest.raises(gen.GenerationUnavailable, match="language la lacks"):
            gen.detect_speech_language(media, "http://ai-openvino:19098",
                                       "a" * 64, str(tmp_path))


def test_raw_gpu_transcript_survives_translation_failure_only(tmp_path, monkeypatch):
    media = tmp_path / "film.mkv"
    media.write_bytes(b"video" * 1024)
    model = tmp_path / "ggml-small.bin"
    model.write_bytes(b"model")
    monkeypatch.setenv("STREMIOSRV_AI_OPENVINO_URL", "http://ai-openvino:19098")
    monkeypatch.setenv("STREMIOSRV_AI_OPENVINO_TOKEN", "a" * 64)
    monkeypatch.setenv("STREMIOSRV_AI_OPENVINO_WORK_DIR", str(tmp_path))
    ih = "a" * 40
    calls = []

    def fake_run(args, **kwargs):
        if args[0] == "ffmpeg":
            Path(args[-1]).write_bytes(b"audio")
        return SimpleNamespace(returncode=0)

    def fake_bridge(folder, *args, **kwargs):
        calls.append(kwargs)
        (folder / "whisper.json").write_text(json.dumps(fake_output("en")))

    monkeypatch.setattr(gen.subprocess, "run", fake_run)
    monkeypatch.setattr(gen, "detect_speech_language", lambda *a, **k: "en")
    monkeypatch.setattr(gen, "run_openvino_bridge", fake_bridge)
    from stremiosrv.subtitle_translator import local_engine

    def failing_translation(*a, **k):
        raise RuntimeError("temporary model failure")

    monkeypatch.setattr(local_engine, "translate_many", failing_translation)
    with pytest.raises(gen.GenerationUnavailable, match="temporary model failure"):
        gen.generate(media, tmp_path, ih, 0, verified_complete=True, model_path=model)
    key = gen.cache_key(ih, 0, 0, model.name)
    raw = tmp_path / ".ai-subtitles" / ih / ("whisper-raw-" + key + ".json")
    assert raw.is_file()
    assert calls and calls[0]["language"] == "en"

    monkeypatch.setattr(local_engine, "translate_many", lambda texts, *a: texts)
    target = gen.generate(media, tmp_path, ih, 0,
                          verified_complete=True, model_path=model)
    assert target.is_file()
    assert not raw.exists()
    assert len(calls) == 1  # no second full-film inference


def test_purging_hash_removes_only_raw_and_generated_artifacts(tmp_path):
    folder = tmp_path / ".ai-subtitles" / ("b" * 40)
    folder.mkdir(parents=True)
    (folder / ("whisper-raw-" + "a"*64 + ".json")).write_text("{}")
    (folder / "metadata.json").write_text("preserve")
    assert gen.delete_hash(tmp_path, "b"*40) == 1
    assert (folder / "metadata.json").exists()
