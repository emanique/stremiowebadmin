"""Conservative local subtitle availability for AI transcription scheduling.

Only inspect subtitles belonging to the exact torrent media, never infer
availability from remote providers or from another torrent's filenames.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

LANGUAGES = {
    "en": "en", "eng": "en", "english": "en",
    "fr": "fr", "fra": "fr", "fre": "fr",
    "es": "es", "spa": "es",
    "de": "de", "deu": "de", "ger": "de",
    "pt": "pt", "por": "pt", "pt-pt": "pt",
    "pt-br": "pb", "por-br": "pb", "pob": "pb",
    "portuguese": "pt",
}

TEXT_CODECS = frozenset({"subrip", "ass", "ssa", "webvtt", "mov_text", "text"})
SIDE_EXT = frozenset({".srt", ".vtt", ".ass", ".ssa", ".sub"})
HASH = re.compile(r"^[a-f0-9]{40}$")


def normalized_language(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return LANGUAGES.get(value.strip().lower().replace("_", "-"))


def local_languages(media: Path, root: Path, infohash: str, *, file_index: int = 0,
                    file_count: int = 1, ffprobe: str = "ffprobe") -> set[str]:
    """Return only positively verified, locally available subtitle tracks."""
    found: set[str] = set()
    media = Path(media)
    root = Path(root)
    if not HASH.fullmatch(infohash) or not media.is_file() or media.is_symlink():
        return found

    # Sidecars must match this exact release basename; ignore generic files
    # whose association to this torrent's video cannot be proven.
    for path in media.parent.iterdir():
        if (not path.is_file() or path.is_symlink() or path.suffix.lower() not in SIDE_EXT
                or not path.name.startswith(media.stem + ".")):
            continue
        language = path.name[len(media.stem) + 1: -len(path.suffix)].split(".", 1)[0]
        lang = normalized_language(language)
        if lang and 0 < path.stat().st_size < 4 * 1024 * 1024:
            found.add(lang)

    # Existing pt-PT translated SRT, explicitly owned by the same infohash.
    translated = root / ".subtitle-translator" / infohash
    if translated.is_dir() and not translated.is_symlink():
        for meta in translated.glob("*.json"):
            if meta.is_symlink() or meta.stat().st_size > 8192:
                continue
            try:
                entry = json.loads(meta.read_text("utf-8"))
                key = entry.get("key")
                owned = (file_count == 1 or
                         entry.get("videoId") == f"stremiosrv:{infohash}:{file_index}")
                if (owned and entry.get("infohash") == infohash
                        and entry.get("target") == "pt-PT"
                        and isinstance(key, str) and re.fullmatch(r"[a-f0-9]{64}", key)
                        and (translated / (key + ".srt")).is_file()):
                    found.add("pt")
            except (OSError, ValueError, TypeError):
                pass

        # Captured originals known to be in English.
        for meta in translated.glob("*.source.json"):
            try:
                if meta.is_symlink() or meta.stat().st_size > 8192:
                    continue
                entry = json.loads(meta.read_text("utf-8"))
                lang = normalized_language(entry.get("sourceLanguage"))
                key = entry.get("id")
                if (file_count == 1 and lang and isinstance(key, str)
                        and re.fullmatch(r"[a-f0-9]{64}", key)
                        and (translated / (key + ".source.srt")).is_file()):
                    found.add(lang)
            except (OSError, ValueError, TypeError):
                pass

    # Embedded text subtitles only; PGS/VobSub require OCR and are excluded.
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "s",
             "-show_entries", "stream=codec_name:stream_tags=language",
             "-of", "json", str(media)],
            capture_output=True, timeout=8, check=False)
        if result.returncode == 0 and len(result.stdout) <= 128 * 1024:
            for stream in json.loads(result.stdout).get("streams", []):
                if not isinstance(stream, dict) or stream.get("codec_name") not in TEXT_CODECS:
                    continue
                lang = normalized_language((stream.get("tags") or {}).get("language"))
                if lang:
                    found.add(lang)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        pass
    return found


def scheduling_priority(languages: set[str], *, translatable: set[str] | None = None) -> tuple[int, str]:
    """Translator first. Whisper only when no usable original-to-PT path exists.

    Presence of a language alone is not enough: the local conversion model
    must be installed and the track actually available for selection.
    """
    if "pt" in languages:
        return 2, "portuguese_available"
    eligible = languages.intersection(translatable if translatable is not None else set())
    if eligible:
        return 1, "subtitle_translator_first"
    return 0, "whisper_fallback_no_translatable_subtitle"


def translatable_languages(languages: set[str]) -> set[str]:
    """Only claim supported conversion where a real offline model path exists."""
    from stremiosrv.subtitle_translator.local_engine import translation_path
    supported = set()
    for language in languages - {"pt"}:
        try:
            if translation_path(language, "pt"):
                supported.add(language)
        except (RuntimeError, ValueError, OSError):
            pass
    return supported
