"""Batch inference and persistent subtitle tests for CPU-only local engine."""
from pathlib import Path

import pytest

from stremiosrv.subtitle_translator import translate_once, translate_srt_batch
from stremiosrv.subtitle_translator.local_engine import (
    installed_pairs, translate_many, LocalModelUnavailable,
)

SRT = ("1\n00:00:01,100 --> 00:00:02,200\nHola\n\n"
       "2\n00:00:03,000 --> 00:00:04,000\nBuenos días\n")
IH = "a" * 40


def test_batch_preserves_indexes_timing_and_cue_count():
    seen = []
    def batch(cues):
        seen.append(cues)
        return ["Olá", "Bom dia"]
    output = translate_srt_batch(SRT, batch)
    assert seen == [["Hola", "Buenos días"]]
    assert "00:00:01,100 --> 00:00:02,200" in output
    assert "00:00:03,000 --> 00:00:04,000" in output
    assert "Olá" in output and "Bom dia" in output


def test_batch_output_count_must_match_cues():
    with pytest.raises(ValueError):
        translate_srt_batch(SRT, lambda cues: ["only one"])


def test_batch_translation_reuses_persistent_cache(tmp_path):
    calls = []
    def batch(cues):
        calls.append(len(cues))
        return ["Olá", "Bom dia"]
    a = translate_once(str(tmp_path), IH, "tt1234567", SRT, "es",
                       translator_many=batch)
    b = translate_once(str(tmp_path), IH, "tt1234567", SRT, "es",
                       translator_many=batch)
    assert calls == [2]
    assert a["cached"] is False
    assert b["cached"] is True
    assert Path(a["file"]).is_file()


def test_model_discovery_requires_complete_directory(tmp_path):
    partial = tmp_path / "es_pt" / "model"
    partial.mkdir(parents=True)
    assert installed_pairs(tmp_path) == set()
    (partial / "model.bin").write_bytes(b"demo")
    (partial / "config.json").write_text("{}")
    (tmp_path / "es_pt" / "sentencepiece.model").write_bytes(b"demo")
    assert installed_pairs(tmp_path) == {("es", "pt")}


def test_missing_model_is_explicit(tmp_path):
    with pytest.raises(LocalModelUnavailable):
        translate_many(["Hola"], "es", root=tmp_path)

def test_model_discovery_accepts_bpe_without_sentencepiece(tmp_path):
    folder = tmp_path / "cs_en"
    (folder / "model").mkdir(parents=True)
    (folder / "model/model.bin").write_bytes(b"test")
    (folder / "model/config.json").write_text("{}")
    (folder / "bpe.model").write_text("#version: 0.2\ns t\n")
    assert installed_pairs(tmp_path) == {("cs", "en")}
