"""Source subtitle normalization, detached from the video transcoding cache."""
from __future__ import annotations

import shutil

import pytest

from stremiosrv.subtitle_translator.normalize import to_srt


def test_native_srt_preserves_timestamps_and_utf8():
    raw = "1\n00:00:01,100 --> 00:00:02,200\nBonjour\n".encode()
    result = to_srt(raw)
    assert "00:00:01,100 --> 00:00:02,200" in result
    assert "Bonjour" in result


def test_reject_empty_and_oversized_source():
    with pytest.raises(ValueError):
        to_srt(b"")
    with pytest.raises(ValueError):
        to_srt(b" " * (4 * 1024 * 1024 + 1))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg unavailable")
def test_vtt_to_srt_is_real_conversion():
    raw = (b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n"
           b"Hello world\n")
    result = to_srt(raw)
    assert "WEBVTT" not in result
    assert "00:00:01,000 --> 00:00:02,000" in result
    assert "Hello world" in result
