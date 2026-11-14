#!/usr/bin/env python3
"""Resolve the literal .env model mount path without shell evaluation."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def resolve(env_file: Path, root: Path) -> Path:
    values = []
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("STREMIOSRV_SUBTITLE_MODELS_DIR="):
            values.append(line.partition("=")[2].strip())
    if len(values) > 1:
        raise ValueError("duplicate subtitle model directory in .env")
    value = os.environ.get("STREMIOSRV_SUBTITLE_MODELS_DIR") or (
        values[0] if values else "./models/subtitles")
    if not value or any(ch in value for ch in ("\x00", "\n", "\r", "\x24", "`")):
        raise ValueError("invalid model directory")
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path.resolve(strict=False)


if __name__ == "__main__":
    print(resolve(Path(sys.argv[1]), Path(sys.argv[2])))
