"""Offline subtitle translation with persistent, title-bound cache.

Deliberately separate from the transcoding cache and My Library addon.
The caller supplies the user-selected SRT text; an addon cannot intercept
another addon's subtitle selection from the standard Stremio protocol.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from typing import Callable

HASH = re.compile(r"^[0-9a-f]{40}$")
LANG = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")
CLOCK = re.compile(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}[,.]\d{3}(?:\s+.*)?$")
MAX_BYTES = 4 * 1024 * 1024
VERSION = "srt-v1"
_LOCK = threading.RLock()


class ModelUnavailable(RuntimeError):
    """The requested language pair has no installed offline model."""



# The introductory cue never changes or delays any translated dialogue.
INTRO_TEXT = (
    "Traduzido automáticamente por Stremio Server Addon/ "
    "Automatic translate by Stremio Server Addon - Reload movie if dont see"
)
INTRO_MARKER = "Traduzido automáticamente por Stremio Server Addon/"
_TIME_LINE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s+-->\s+")


_SOURCE_LANGUAGE_NAMES = {
    'en':'Inglês','fr':'Francês','es':'Espanhol','de':'Alemão',
    'ru':'Russo','pl':'Polaco','nl':'Neerlandês','bg':'Búlgaro',
    'el':'Grego','bn':'Bengali','et':'Estónio','eu':'Basco',
    'gl':'Galego','ky':'Quirguiz','lv':'Letão','ms':'Malaio',
    'nb':'Norueguês','sk':'Eslovaco','sl':'Esloveno','sq':'Albanês',
    'sw':'Suaíli','th':'Tailandês','tl':'Tagalo','vi':'Vietnamita',
    'zh':'Chinês','zt':'Chinês tradicional','pb':'Português do Brasil',
}


def _source_language_label(source: str | None) -> str:
    if not source or not LANG.fullmatch(source):
        return ''
    code = source.lower()
    return f"{_SOURCE_LANGUAGE_NAMES.get(code, code.upper())} ({code.upper()})"


def _intro_text(source: str | None) -> str:
    label = _source_language_label(source)
    if not label:
        return INTRO_TEXT
    return (INTRO_TEXT + '\n'
            + f'Traduzido de {label} para Português (pt-PT) / '
            + f'Translated from {label} to Portuguese (pt-PT)')


def _with_translation_intro(srt: str, source: str | None = None) -> str:
    """Annotate translated SRT without changing original cue timings.

    Already-cached intro cues are upgraded in place without adding a second
    intro. For new translations, show from t=0 until 1s before the first cue.
    """
    normalized = srt.replace('\r\n','\n').replace('\r','\n').lstrip('\ufeff')
    if INTRO_MARKER in normalized[:750]:
        # Existing subtitle introductory cue: edit text ONLY, preserve timings
        # and every dialogue cue. This also keeps cache updates idempotent.
        if not source:
            return srt
        blocks = re.split(r'(\n\s*\n)', normalized, maxsplit=1)
        if not blocks or INTRO_MARKER not in blocks[0]:
            return srt
        lines = blocks[0].split('\n')
        if len(lines) < 3 or not _TIME_LINE.match(lines[1].strip()):
            return srt
        updated = '\n'.join(lines[:2] + [_intro_text(source)])
        result = updated + ''.join(blocks[1:])
        return result if result != normalized else srt
    first = next((_TIME_LINE.match(line.strip()) for line in normalized.splitlines()
                  if _TIME_LINE.match(line.strip())), None)
    if first is None:
        return srt
    h,m,sec,ms = map(int, first.groups())
    first_ms = ((h*60+m)*60+sec)*1000+ms
    cutoff = first_ms - 1000
    if cutoff <= 0:
        return srt
    end_h,rest = divmod(cutoff,3600000)
    end_m,rest = divmod(rest,60000)
    end_s,end_ms = divmod(rest,1000)
    end_stamp = f'{end_h:02d}:{end_m:02d}:{end_s:02d},{end_ms:03d}'
    original = re.split(r'\n\s*\n',normalized.strip())
    blocks=[]
    for block in original:
        items=block.split('\n')
        if items and items[0].strip().isdigit() and len(items)>1 and _TIME_LINE.match(items[1].strip()):
            items[0]=str(len(blocks)+2)
        blocks.append('\n'.join(items))
    intro=f'1\n00:00:00,000 --> {end_stamp}\n{_intro_text(source)}'
    return intro+'\n\n'+'\n\n'.join(blocks)+'\n'


def ensure_translation_intro(path: Path, source: str | None = None) -> None:
    """Upgrade cached SRT header, reading the source from its own metadata."""
    if not path.is_file() or path.is_symlink():
        return
    if source is None:
        meta = path.with_suffix('.json')
        if meta.is_file() and not meta.is_symlink():
            try:
                record=json.loads(meta.read_text('utf-8'))
                source=record.get('source') if record.get('key')==path.stem else None
            except (OSError, ValueError, TypeError):
                pass
    content=path.read_text('utf-8')
    updated=_with_translation_intro(content,source)
    if updated!=content:
        _atomic(path,updated)


def translate_srt(text: str, translate: Callable[[str], str]) -> str:
    """Translate only cue payloads, preserving exact timestamps and indexes."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("invalid or oversized subtitle")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    blocks = re.split(r"(\n\s*\n)", normalized)
    seen = 0
    out: list[str] = []
    for block in blocks:
        if not block.strip():
            out.append(block)
            continue
        lines = block.split("\n")
        timing = next((i for i, line in enumerate(lines[:3]) if CLOCK.fullmatch(line.strip())), None)
        if timing is None:
            # Preserve VTT headers/notes, but do not pass them through a translator.
            out.append(block)
            continue
        indexes = lines[:timing + 1]
        payload = lines[timing + 1:]
        if not any(x.strip() for x in payload):
            out.append(block)
            continue
        original = "\n".join(payload)
        trailing = original[len(original.rstrip("\n")):]
        result = translate(original.rstrip("\n"))
        if not isinstance(result, str) or not result.strip():
            raise ValueError("translator returned empty subtitle")
        if "-->" in result:
            raise ValueError("translator returned subtitle timing")
        out.append("\n".join([*indexes, result + trailing]))
        seen += 1
    if not seen:
        raise ValueError("subtitle has no SRT cues")
    return "".join(out)


@lru_cache(maxsize=32)
def _offline_model(source: str, target: str):
    """Resolve installed model once per language pair, not once per subtitle cue."""
    try:
        from argostranslate import translate
    except ImportError as exc:
        raise ModelUnavailable("argostranslate is not installed") from exc
    languages = translate.get_installed_languages()
    by_code = {lang.code: lang for lang in languages}
    src = source.split("-")[0].lower()
    dst = target.split("-")[0].lower()
    if src not in by_code or dst not in by_code:
        raise ModelUnavailable(f"offline model unavailable: {src} -> {dst}")
    candidate = by_code[src].get_translation(by_code[dst])
    if candidate is None:
        raise ModelUnavailable(f"offline model unavailable: {src} -> {dst}")
    return candidate


def available_source_languages(target: str = "pt-PT") -> set[str]:
    """Return source languages with installed local translations to target.

    No network access, no model installation, no cloud fallback.
    """
    from stremiosrv.subtitle_translator.local_engine import supported_source_languages
    destination_code = target.split("-")[0].lower()
    found = supported_source_languages() if destination_code == "pt" else set()
    try:
        from argostranslate import translate
    except ImportError:
        return found
    installed = translate.get_installed_languages()
    destination = next(
        (lang for lang in installed if lang.code == destination_code), None
    )
    if destination is not None:
        found.update(
            lang.code for lang in installed
            if lang.code != destination.code
            and lang.get_translation(destination) is not None
        )
    return found


def translate_srt_batch(text: str, translate_many: Callable[[list[str]], list[str]]) -> str:
    """Batch-translate subtitle cue payloads without changing SRT timings."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("invalid or oversized subtitle")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    blocks = re.split(r"(\n\s*\n)", normalized)
    cues: list[tuple[int, list[str], str]] = []
    to_translate: list[str] = []
    for block_index, block in enumerate(blocks):
        if not block.strip():
            continue
        lines = block.split("\n")
        timing = next((i for i, line in enumerate(lines[:3]) if CLOCK.fullmatch(line.strip())), None)
        if timing is None or not any(x.strip() for x in lines[timing + 1:]):
            continue
        original = "\n".join(lines[timing + 1:])
        trailing = original[len(original.rstrip("\n")):]
        payload = original.rstrip("\n")
        if len(payload) > 8000:
            raise ValueError("subtitle cue exceeds model input limit")
        cues.append((block_index, lines[:timing + 1], trailing))
        to_translate.append(payload)
    if not cues:
        raise ValueError("subtitle has no SRT cues")
    results = translate_many(to_translate)
    if len(results) != len(cues):
        raise ValueError("translator returned wrong number of cues")
    for (block_index, indexes, trailing), result in zip(cues, results):
        if not isinstance(result, str) or not result.strip() or "-->" in result:
            raise ValueError("translator returned invalid text")
        blocks[block_index] = "\n".join([*indexes, result + trailing])
    return "".join(blocks)


def offline_argos(text: str, source: str, target: str = "pt-PT") -> str:
    """Translate locally, reusing the installed Argos model for every cue."""
    if source.split("-")[0].lower() == "auto":
        raise ModelUnavailable("source language must be identified by the provider")
    return _offline_model(source, target).translate(text)


def _validate(infohash: str, video_id: str, source: str, target: str) -> None:
    if not HASH.fullmatch(infohash):
        raise ValueError("invalid infohash")
    if not video_id or len(video_id) > 256 or any(c in video_id for c in "\r\n"):
        raise ValueError("invalid video id")
    if not LANG.fullmatch(source) or not LANG.fullmatch(target):
        raise ValueError("invalid language code")


def _folder(cache_root: str) -> Path:
    return Path(cache_root) / ".subtitle-translator"


def _key(infohash: str, video_id: str, source: str, target: str, text: str,
         selection_id: str | None = None) -> str:
    payload = json.dumps([VERSION, infohash, video_id, source, target,
                          hashlib.sha256(text.encode("utf-8")).hexdigest(),
                          selection_id],
                         separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _atomic(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def translate_once(cache_root: str, infohash: str, video_id: str, original: str,
                   source: str, target: str = "pt-PT",
                   translator: Callable[[str], str] | None = None,
                   selection_id: str | None = None,
                   translator_many: Callable[[list[str]], list[str]] | None = None,
                   source_name: str = "", source_addon: str = "") -> dict:
    """Translate once. Caller must verify infohash exists in the principal cache.

    A single process lock prevents parallel duplicate translations. For multi-worker
    deployments use a process-shared lock before enabling this endpoint.
    """
    _validate(infohash, video_id, source, target)
    if len(original.encode("utf-8")) > MAX_BYTES:
        raise ValueError("subtitle exceeds 4 MiB")
    if selection_id is not None and not re.fullmatch(r"[0-9a-f]{64}", selection_id):
        raise ValueError("invalid source selection")
    key = _key(infohash, video_id, source, target, original, selection_id)
    directory = _folder(cache_root) / infohash
    path = directory / (key + ".srt")
    meta = directory / (key + ".json")
    with _LOCK:
        if path.is_file() and meta.is_file():
            ensure_translation_intro(path)
            return {"key": key, "cached": True, "file": str(path)}
        if source.split("-")[0] == target.split("-")[0]:
            rendered = original
        else:
            if translator_many is None and translator is None:
                from stremiosrv.subtitle_translator.local_engine import (
                    translation_path, translate_many,
                )
                # A French subtitle may use a validated local
                # fr->en->pt route rather than a direct fr->pt package.
                if translation_path(source, target):
                    translator_many = lambda cues: translate_many(cues, source, target)
            if translator_many is not None:
                rendered = translate_srt_batch(original, translator_many)
            else:
                if translator is None:
                    translator = lambda cue: offline_argos(cue, source, target)
                rendered = translate_srt(original, translator)
        rendered = _with_translation_intro(rendered, source)
        _atomic(path, rendered)
        _atomic(meta, json.dumps({"infohash": infohash, "videoId": video_id,
                                  "source": source, "target": target, "key": key,
                                  "selectionId": selection_id,
                                  "sourceName": source_name[:120],
                                  "sourceAddon": source_addon[:120]}))
        return {"key": key, "cached": False, "file": str(path)}


def list_cached(cache_root: str, infohash: str, video_id: str, target: str = "pt-PT") -> list[dict]:
    _validate(infohash, video_id, "en", target)
    directory = _folder(cache_root) / infohash
    found: list[dict] = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text("utf-8"))
            key = data.get("key")
            if (isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key)
                    and data.get("target") == target
                    and data.get("infohash") == infohash
                    # Cache is owned by the verified torrent infohash, not by
                    # whichever alias (IMDb or stremiosrv:hash) the App uses.
                    # Both aliases are independently resolved to this title
                    # by the addon router before list_cached is called.
                    and (directory / (key + ".srt")).is_file()):
                found.append(data)
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda x: x["key"])


def delete_title(cache_root: str, infohash: str) -> int:
    """Explicit removal hook to call only AFTER principal-cache title removal."""
    if not HASH.fullmatch(infohash):
        raise ValueError("invalid infohash")
    directory = _folder(cache_root) / infohash
    count = 0
    with _LOCK:
        if directory.exists() and directory.is_dir() and not directory.is_symlink():
            for path in directory.iterdir():
                if path.is_file() and not path.is_symlink() and path.suffix in (".json", ".srt"):
                    path.unlink()
                    count += 1
            if not any(directory.iterdir()):
                directory.rmdir()
    return count
