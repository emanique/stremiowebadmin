"""Live embedded English ASS track appears as lazy pt-PT SRT option."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from stremiosrv.subtitle_translator import addon


def test_embedded_ass_enumerated_and_translated(tmp_path, monkeypatch):
    media = tmp_path / "Example.Movie.mkv"
    source = tmp_path / "english.ass"
    source.write_text(
        "[Script Info]\nScriptType: v4.00+\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello my friend.\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         "color=c=black:s=128x72:d=4:r=1", "-i", str(source),
         "-map", "0:v:0", "-map", "1:0",
         "-c:v", "mpeg4", "-c:s", "ass", "-metadata:s:s:0",
         "language=eng", "-y", str(media)], capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")[:500]
    ih = "e" * 40
    monkeypatch.setattr(addon.cache, "load_name_index",
                        lambda root: {media.name: ih})
    monkeypatch.setattr(addon, "_guard", lambda *args: None)
    monkeypatch.setattr(addon, "_origin", lambda r: "https://stream.example.test")
    monkeypatch.setattr(addon.subtitle_sources, "configured_sources", lambda _: "")
    monkeypatch.setattr(addon, "available_source_languages", lambda: {"en"})
    monkeypatch.setattr(addon, "_translation_preferences", lambda: ("en",))
    from stremiosrv.subtitle_translator import local_engine
    calls = []
    def fake_translate(texts, source, target="pt-PT"):
        calls.append((source, texts))
        return ["Olá, meu amigo." for _ in texts]
    monkeypatch.setattr(local_engine, "translation_path",
                        lambda *a: (("en", "pt"),))
    monkeypatch.setattr(local_engine, "translate_many", fake_translate)
    app = FastAPI()
    app.state.settings = SimpleNamespace(cache_root=str(tmp_path))
    app.include_router(addon.router)
    client = TestClient(app)
    video_id = "stremiosrv:" + ih
    listing = client.get(f"/subtitle-translator/test/subtitles/other/{video_id}.json")
    assert listing.status_code == 200, listing.text
    options = listing.json()["subtitles"]
    assert len(options) == 1, options
    assert options[0]["lang"] == "pt-PT"
    assert "EN → PT" in options[0]["name"]
    assert "embutida" in options[0]["name"]
    selected = urlsplit(options[0]["url"])
    first = client.get(selected.path + ("?" + selected.query if selected.query else ""))
    assert first.status_code == 200, first.text
    assert "Olá, meu amigo." in first.text
    assert "00:00:01,000 --> 00:00:03,000" in first.text
    assert len(calls) == 1
    again = client.get(selected.path + ("?" + selected.query if selected.query else ""))
    assert again.status_code == 200 and first.text == again.text
    assert len(calls) == 1
