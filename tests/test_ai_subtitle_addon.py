"""Integration contract for the independently installable AI subtitle addon."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from stremiosrv.ai_subtitles import addon
from stremiosrv.ai_subtitles.generator import cache_key


def test_generated_track_is_labelled_with_addon_and_pt_pt(tmp_path, monkeypatch):
    hash_value = "a" * 40
    filename = "ggml-small.bin"
    monkeypatch.setenv("STREMIOSRV_AI_WHISPER_MODEL", "/opt/whisper/" + filename)
    key = cache_key(hash_value, 0, 0, filename)
    folder = tmp_path / ".ai-subtitles" / hash_value
    folder.mkdir(parents=True)
    (folder / (key + ".srt")).write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nBom dia.\n", encoding="utf8"
    )
    handle = SimpleNamespace(has_metadata=lambda: True, num_files=lambda: 1)
    engine = SimpleNamespace(get=lambda _: handle)
    settings = SimpleNamespace(cache_root=str(tmp_path))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=settings, engine=engine)))
    with (patch.object(addon.translator, "_guard"),
          patch.object(addon.translator, "_linked_hashes", return_value=[hash_value]),
          patch.object(addon.translator, "_title_present", return_value=True),
          patch.object(addon.translator, "_origin", return_value="https://example.invalid")):
        response = addon.subtitles("secret", "movie", f"stremiosrv:{hash_value}", request)
    assert response["subtitles"] == [{
        "id": key,
        "lang": "pt-PT",
        "name": "AI Subtitle Generator · Português (pt-PT)",
        "url": f"https://example.invalid/ai-subtitles/secret/file/{hash_value}/{key}.srt",
    }]


def test_explicit_multi_file_id_works_after_torrent_handle_unloads(tmp_path, monkeypatch):
    """Do not discard a cached subtitle when its torrent handle is absent."""
    ih = "b" * 40
    model = "ggml-small.bin"
    index = 3
    monkeypatch.setenv("STREMIOSRV_AI_WHISPER_MODEL", "/models/" + model)
    key = cache_key(ih, index, 0, model)
    folder = tmp_path / ".ai-subtitles" / ih
    folder.mkdir(parents=True)
    (folder / (key + ".srt")).write_text("1\n00:00:00,000 --> 00:00:01,000\nOlá.\n")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=SimpleNamespace(cache_root=str(tmp_path)),
        engine=SimpleNamespace(get=lambda _: None))))
    with (patch.object(addon.translator, "_guard"),
          patch.object(addon.translator, "_linked_hashes",
                       side_effect=AssertionError("handle-dependent discovery called")),
          patch.object(addon.translator, "_title_present", return_value=True),
          patch.object(addon.translator, "_origin", return_value="https://example.invalid")):
        response = addon.subtitles("secret", "series", f"stremiosrv:{ih}:{index}", request)
    assert len(response["subtitles"]) == 1
    assert response["subtitles"][0]["lang"] == "pt-PT"
    assert response["subtitles"][0]["id"] == key
