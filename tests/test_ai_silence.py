"""Contract tests for opt-in empty audio transcription in on-demand AI."""
from __future__ import annotations

import pytest

from stremiosrv.ai_subtitles.generator import GenerationUnavailable, json_to_srt


def test_valid_empty_transcription_only_on_demand():
    output = {"result": {"language": "english"}, "transcription": []}
    assert json_to_srt(output, allow_silence=True) == ("", "en")
    with pytest.raises(GenerationUnavailable, match="transcription empty"):
        json_to_srt(output)


def test_nonempty_invalid_segments_never_become_silence():
    output = {"result": {"language": "en"}, "transcription": [{"text": "bad timings"}]}
    with pytest.raises(GenerationUnavailable, match="transcription empty"):
        json_to_srt(output, allow_silence=True)


def test_missing_transcription_is_error_even_when_silence_allowed():
    with pytest.raises(GenerationUnavailable, match="no transcription"):
        json_to_srt({"result": {"language": "en"}}, allow_silence=True)


def test_valid_cue_unchanged():
    output = {"result": {"language": "english"}, "transcription": [
        {"offsets": {"from": 1000, "to": 2600}, "text": "Speech"}
    ]}
    srt, language = json_to_srt(output, allow_silence=True)
    assert language == "en"
    assert "00:00:01,000 --> 00:00:02,600" in srt
    assert "Speech" in srt


def test_empty_cached_block_is_ready_and_can_be_skipped(tmp_path):
    from stremiosrv.ai_subtitles.on_demand import DemandSubtitleSessions
    folder = tmp_path / "audio" / "0"
    folder.mkdir(parents=True)
    # A one-newline marker is a valid finished silent block, not a caption.
    (folder / "chunk-00000000.srt").write_text("\n", encoding="utf8")
    assert DemandSubtitleSessions._block_ready(folder, 0)
    assert not DemandSubtitleSessions._block_ready(folder, 30)
