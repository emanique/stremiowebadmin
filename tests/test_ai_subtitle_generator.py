"""Offline AI subtitle generator unit tests; no model downloads or network."""
import pytest

from stremiosrv.ai_subtitles.generator import (
    GenerationUnavailable, cache_key, delete_hash, generate, json_to_srt,
)


def test_cache_identity_is_release_file_audio_model_specific():
    ih = "a" * 40
    one = cache_key(ih, 0, 0, "ggml-small.bin")
    assert len(one) == 64
    assert one != cache_key(ih, 1, 0, "ggml-small.bin")
    assert one != cache_key(ih, 0, 1, "ggml-small.bin")
    assert one != cache_key(ih, 0, 0, "ggml-medium.bin")
    with pytest.raises(ValueError):
        cache_key("../bad", 0, 0, "ggml-small.bin")


def test_completed_torrent_is_mandatory(tmp_path):
    media = tmp_path / "film.mkv"
    media.write_bytes(b"fake")
    with pytest.raises(GenerationUnavailable, match="completion"):
        generate(media, tmp_path, "a" * 40, 0)


def test_whisper_json_to_srt():
    payload = {
        "result": {"language": "en"},
        "transcription": [
            {"offsets": {"from": 300, "to": 1200}, "text": "Hello"},
            {"offsets": {"from": 1500, "to": 2500}, "text": "World"},
        ],
    }
    srt, lang = json_to_srt(payload)
    assert lang == "en"
    assert "00:00:00,300 --> 00:00:01,200" in srt
    assert "00:00:01,500 --> 00:00:02,500" in srt
    assert "Hello" in srt


def test_invalid_segments_do_not_create_subtitles():
    with pytest.raises(GenerationUnavailable):
        json_to_srt({"result": {"language": "en"}, "transcription": [
            {"offsets": {"from": 5000, "to": 3000}, "text": "backward"}
        ]})


def test_cleanup_restricted_to_expected_hash(tmp_path):
    folder = tmp_path / ".ai-subtitles" / ("b" * 40)
    folder.mkdir(parents=True)
    (folder / "one.srt").write_text("subtitle", encoding="utf-8")
    (folder / "keep.json").write_text("metadata", encoding="utf-8")
    assert delete_hash(tmp_path, "b" * 40) == 1
    assert (folder / "keep.json").exists()
    with pytest.raises(ValueError):
        delete_hash(tmp_path, "../outside")


def test_removed_media_during_transcription_never_resurrects_srt(tmp_path, monkeypatch):
    """Regression: torrent cache deletion while Whisper is running."""
    import json
    import subprocess
    from pathlib import Path
    from stremiosrv.ai_subtitles import generator as gen

    media = tmp_path / "movie.mkv"
    media.write_bytes(b"test video")
    model = tmp_path / "ggml-small.bin"
    model.write_bytes(b"model")

    def fake_run(args, **kwargs):
        if "-map" in args:
            (tmp_path / "mock_audio_wav").write_bytes(b"audio")
        if "-oj" in args:
            # The media disappears after audio extraction but before SRT publish.
            media.unlink()
            output = Path(args[args.index("-of") + 1]).with_suffix(".json")
            output.write_text(json.dumps({
                "result": {"language": "pt"},
                "transcription": [{
                    "offsets": {"from": 0, "to": 1500}, "text": "Bom dia."
                }],
            }))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(gen.subprocess, "run", fake_run)
    with pytest.raises(GenerationUnavailable, match="removed"):
        generate(media, tmp_path, "a" * 40, 0, verified_complete=True,
                 model_path=model, whisper_bin="mock-whisper")
    assert not (tmp_path / ".ai-subtitles" / ("a" * 40)).exists()


def test_changed_media_during_transcription_is_rejected(tmp_path, monkeypatch):
    """Replacing a video with different content must not preserve stale captions."""
    import json
    import subprocess
    from pathlib import Path
    from stremiosrv.ai_subtitles import generator as gen

    media = tmp_path / "movie.mkv"
    media.write_bytes(b"original video")
    model = tmp_path / "ggml-small.bin"
    model.write_bytes(b"model")

    def fake_run(args, **kwargs):
        if "-oj" in args:
            media.write_bytes(b"replaced video")
            output = Path(args[args.index("-of") + 1]).with_suffix(".json")
            output.write_text(json.dumps({
                "result": {"language": "pt"},
                "transcription": [{
                    "offsets": {"from": 0, "to": 1500}, "text": "Bom dia."
                }],
            }))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(gen.subprocess, "run", fake_run)
    with pytest.raises(GenerationUnavailable, match="changed"):
        generate(media, tmp_path, "a" * 40, 0, verified_complete=True,
                 model_path=model, whisper_bin="mock-whisper")
    assert not (tmp_path / ".ai-subtitles" / ("a" * 40)).exists()
