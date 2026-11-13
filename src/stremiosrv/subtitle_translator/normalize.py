"""Convert subtitle source payloads to strict UTF-8 SRT.

The player may serve .vtt, .ass or .ssa; we must not announce WEBVTT/ASS
text as application/x-subrip. FFmpeg is invoked for non-SRT sources with
local-files-only protocol access and a hard timeout. No network is available
to the converter.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from stremiosrv.api.subs import decode_subtitle

MAX_BYTES = 4 * 1024 * 1024


def to_srt(raw: bytes, content_type: str = "") -> str:
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError("subtitle exceeds maximum size")
    text = decode_subtitle(raw)
    if not text.strip():
        raise ValueError("subtitle is empty")
    simple_srt = ("-->" in text
                  and not text.lstrip("\ufeff \r\n").startswith("WEBVTT"))
    if simple_srt and "[Script Info]" not in text[:100]:
        return text
    # Non-SRT subtitle content needs actual conversion, not a renamed extension.
    suffix = (".vtt" if text.lstrip("\ufeff \r\n").startswith("WEBVTT")
              else ".ass" if "[Script Info]" in text[:300] else ".sub")
    name = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         suffix=suffix, delete=False) as source:
            name = source.name
            source.write(text)
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-protocol_whitelist", "file,crypto,data",
             "-i", name, "-f", "srt", "pipe:1"],
            capture_output=True, timeout=15, check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            raise ValueError("unable to convert selected subtitle to SRT")
        if len(proc.stdout) > MAX_BYTES:
            raise ValueError("converted subtitle exceeds maximum size")
        converted = proc.stdout.decode("utf-8", "replace")
        if "-->" not in converted:
            raise ValueError("converted subtitle has no timed cues")
        return converted
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("subtitle conversion is unavailable") from exc
    finally:
        if name:
            Path(name).unlink(missing_ok=True)
