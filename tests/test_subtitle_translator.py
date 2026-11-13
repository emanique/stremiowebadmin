"""Regression tests for standalone persistent subtitle translation."""
import importlib.util
from pathlib import Path

import pytest

from stremiosrv.subtitle_translator import (
    ModelUnavailable,
    delete_title,
    list_cached,
    translate_once,
    translate_srt,
)

IH = "a" * 40
VIDEO = "tt1234567:1"
SRT = "1\n00:00:01,000 --> 00:00:04,200\nHello world\n\n2\n00:00:05,000 --> 00:00:06,000\nGood morning\n"


def test_timestamps_unchanged():
    result = translate_srt(SRT, lambda s: {"Hello world": "Olá mundo",
                                          "Good morning": "Bom dia"}[s])
    assert "00:00:01,000 --> 00:00:04,200" in result
    assert "00:00:05,000 --> 00:00:06,000" in result
    assert "Olá mundo" in result and "Bom dia" in result
    assert result.startswith("1\n")


def test_translation_cached_without_reinvoking_engine(tmp_path):
    calls = []
    def translate(cue):
        calls.append(cue)
        return "Traduzido: " + cue
    first = translate_once(str(tmp_path), IH, VIDEO, SRT, "en", translator=translate)
    assert first["cached"] is False
    assert len(calls) == 2
    second = translate_once(str(tmp_path), IH, VIDEO, SRT, "en", translator=translate)
    assert second["cached"] is True
    assert len(calls) == 2
    assert Path(first["file"]).read_text() == Path(second["file"]).read_text()
    assert len(list_cached(str(tmp_path), IH, VIDEO)) == 1


def test_source_or_video_changes_key(tmp_path):
    t = lambda s: "translated " + s
    a = translate_once(str(tmp_path), IH, VIDEO, SRT, "en", translator=t)
    b = translate_once(str(tmp_path), IH, VIDEO + "x", SRT, "en", translator=t)
    c = translate_once(str(tmp_path), IH, VIDEO, SRT, "es", translator=t)
    assert len({a["key"], b["key"], c["key"]}) == 3


def test_deletion_scoped_to_title(tmp_path):
    t = lambda s: s.upper()
    a = translate_once(str(tmp_path), IH, VIDEO, SRT, "en", translator=t)
    other = translate_once(str(tmp_path), "b" * 40, VIDEO, SRT, "en", translator=t)
    assert delete_title(str(tmp_path), IH) == 2
    assert not Path(a["file"]).exists()
    assert Path(other["file"]).exists()


def test_reject_unsafe_infohash(tmp_path):
    with pytest.raises(ValueError):
        translate_once(str(tmp_path), "../", VIDEO, SRT, "en", translator=str.upper)
    with pytest.raises(ValueError):
        delete_title(str(tmp_path), "../../secret")


def test_no_cue_and_injected_clock_rejected():
    with pytest.raises(ValueError):
        translate_srt("not a subtitle", str.upper)
    with pytest.raises(ValueError):
        translate_srt(SRT, lambda _: "00:00:02,000 --> 00:00:03,000")


def test_offline_model_required_when_absent(tmp_path):
    if importlib.util.find_spec("argostranslate") is not None:
        pytest.skip("optional local model package installed; integration belongs on test host")
    with pytest.raises(ModelUnavailable):
        translate_once(str(tmp_path), IH, VIDEO, SRT, "en")
