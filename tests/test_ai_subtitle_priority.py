"""Whisper only handles sources the Subtitle Translator cannot serve."""
from unittest.mock import Mock, patch
from stremiosrv.ai_subtitles.priority import scheduling_priority, normalized_language
from stremiosrv.ai_subtitles.service import AutoSubtitleWorker


def test_translator_precedes_whisper_when_pt_can_be_derived():
    assert scheduling_priority(set(), translatable=set())[0] == 0
    assert scheduling_priority({"en"}, translatable={"en"}) == (1, "subtitle_translator_first")
    assert scheduling_priority({"fr"}, translatable={"fr"})[0] == 1
    assert scheduling_priority({"en"}, translatable=set())[0] == 0
    assert scheduling_priority({"pt"}, translatable=set()) == (2, "portuguese_available")
    assert normalized_language("eng") == "en"
    assert normalized_language("pt-BR") == "pb"
    assert normalized_language("fra") == "fr"
    assert scheduling_priority({"pb"}, translatable={"pb"})[0] == 1
    assert scheduling_priority({"pb"}, translatable=set())[0] == 0


def test_worker_skips_whisper_for_translatable_english(tmp_path, monkeypatch):
    (tmp_path / "film.mkv").write_bytes(b"fake")
    model = tmp_path / "ggml-small.bin"
    model.write_bytes(b"fake")
    monkeypatch.setenv("STREMIOSRV_AI_WHISPER_MODEL", str(model))
    handle = Mock()
    handle.info_hash.return_value = "a" * 40
    handle.has_metadata.return_value = True
    handle.file_paths.return_value = ["film.mkv"]
    handle.file_complete.return_value = True
    engine = Mock()
    engine.active.return_value = [handle]
    worker = AutoSubtitleWorker(engine, str(tmp_path))
    with patch("stremiosrv.ai_subtitles.service.local_languages", return_value={"en"}), patch(
        "stremiosrv.ai_subtitles.service.translatable_languages", return_value={"en"}
    ), patch("stremiosrv.ai_subtitles.service.generate") as generate:
        assert worker.scan_once() is False
        generate.assert_not_called()
        assert worker.snapshot()["jobs"][0]["status"] == "subtitle_translator_first"


def test_cached_translation_is_file_specific_in_multi_episode_pack(tmp_path, monkeypatch):
    """A translation of episode 1 must not suppress fallback for episode 2."""
    import json
    from pathlib import Path
    from stremiosrv.ai_subtitles.priority import local_languages
    from unittest.mock import patch
    ih = "e" * 40
    video = tmp_path / "episode2.mkv"
    video.write_bytes(b"media")
    directory = tmp_path / ".subtitle-translator" / ih
    directory.mkdir(parents=True)
    key = "a" * 64
    (directory / (key + ".srt")).write_text("1\n00:00:00,000 --> 00:00:01,000\nOlá\n")
    (directory / (key + ".json")).write_text(json.dumps({
        "infohash": ih, "target": "pt-PT",
        "videoId": f"stremiosrv:{ih}:0", "key": key
    }))
    with patch("stremiosrv.ai_subtitles.priority.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = b'{"streams":[]}'
        assert "pt" not in local_languages(video, tmp_path, ih, file_index=1, file_count=2)
        assert "pt" in local_languages(video, tmp_path, ih, file_index=0, file_count=2)


def test_translatable_sidecar_has_priority_without_starting_whisper(tmp_path, monkeypatch):
    from stremiosrv.ai_subtitles.priority import local_languages
    from unittest.mock import patch
    media = tmp_path / "film.mkv"
    media.write_bytes(b"media")
    (tmp_path / "film.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    with patch("stremiosrv.ai_subtitles.priority.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = b'{"streams":[]}'
        assert local_languages(media, tmp_path, "f"*40) == {"en"}
