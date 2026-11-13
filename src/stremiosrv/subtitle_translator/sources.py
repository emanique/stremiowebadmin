"""Subtitle source discovery using configured Stremio addon manifests.

Discovery requires Internet unless the upstream addon is independently hosted.
Translation remains offline. URLs are never supplied directly by clients:
only server-configured HTTPS manifests are consulted. Network access uses
the server's pinned-IP/redirect-checked destination guard in public-only mode.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit, urlunsplit

from stremiosrv.proxy import upstream

MAX_JSON = 1024 * 1024
MAX_TEXT = 4 * 1024 * 1024
MAX_SOURCES = 3
_LANG = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")
_VIDEO = re.compile(r"^(?:tt\d{2,12}(?::\d{1,4}:\d{1,4})?|stremiosrv:[a-f0-9]{40}(?::\d{1,5})?)$")
_UA = "SubtitleTranslatorLocal/0.1 (Stremio)"

# OpenSubtitles v3 uses ISO-639-2/B in its "lang" field (e.g. ger).
# Offline Argos models generally use ISO-639-1 two-letter codes.
_ISO639 = {
    "eng": "en", "spa": "es", "ger": "de", "deu": "de",
    "fre": "fr", "fra": "fr", "ita": "it", "rus": "ru",
    "dut": "nl", "nld": "nl", "pol": "pl", "por": "pt",
    "jpn": "ja", "kor": "ko", "chi": "zh", "zho": "zh",
    "ara": "ar", "tur": "tr", "ukr": "uk", "swe": "sv",
    "fin": "fi", "dan": "da", "nor": "nb", "hun": "hu",
    "cze": "cs", "ces": "cs", "rum": "ro", "ron": "ro",
    "gre": "el", "ell": "el", "heb": "he", "hin": "hi",
    "ind": "id", "vie": "vi", "tha": "th",
    # Extend ISO-639-2 mappings to all Argos source pairs. The provider may
    # return three-letter language tags rather than the model's two-letter ID.
    "sqi": "sq", "alb": "sq", "aze": "az", "eus": "eu", "baq": "eu",
    "ben": "bn", "bul": "bg", "cat": "ca", "dan": "da", "epo": "eo",
    "est": "et", "fin": "fi", "glg": "gl", "gle": "ga", "hun": "hu",
    "kir": "ky", "lav": "lv", "lit": "lt", "msa": "ms", "may": "ms",
    "nob": "nb", "fas": "fa", "per": "fa", "ron": "ro", "slk": "sk",
    "slo": "sk", "slv": "sl", "swa": "sw", "tgl": "tl", "urd": "ur",
}


def normalize_language(code: str) -> str | None:
    """Translate ISO-639-2 and localized tags to an Argos-compatible code."""
    if not isinstance(code, str):
        return None
    raw = code.strip().replace("_", "-").lower()
    # Argos publishes pt-BR as `pb`, distinct from the Portuguese target.
    # Only Brazilian-marked sources are eligible; ordinary `pt` is not translated.
    if raw in ("pt-br", "por-br"):
        return "pb"
    if raw in ("zh-tw", "zh-hant", "zho-tw", "zho-hant"):
        return "zt"
    if raw in ("no", "nb", "nob"):
        return "nb"
    if not _LANG.fullmatch(raw):
        return None
    base, sep, region = raw.partition("-")
    translated = _ISO639.get(base, base)
    if len(translated) != 2:
        return None  # unknown three-letter codes must not be advertised as supported
    return translated + ("-" + region if sep else "")



def configured_sources(settings) -> str:
    """Read current WebAdmin config, without relying on an image-specific overlay.

    The core mounts admin-settings.json read-only. WebAdmin may change it after
    process startup, therefore read the current file at each subtitle request.
    No arbitrary client-supplied paths or URLs are accepted.
    """
    fallback = str(getattr(settings, "subtitle_source_addons", "") or "")
    config_path = Path(os.environ.get(
        "STREMIOSRV_EXTERNAL_CONFIG", "/config/admin-settings.json"
    ))
    try:
        payload = json.loads(config_path.read_text("utf-8"))
    except (OSError, ValueError):
        return fallback
    if not isinstance(payload, dict):
        return fallback
    # WebAdmin stores flat settings; tolerate an embedded settings object if present.
    values = payload.get("settings", payload)
    if not isinstance(values, dict):
        return fallback
    chosen = values.get("subtitle_source_addons", fallback)
    return chosen if isinstance(chosen, str) else fallback


def provider_roots(config: str) -> tuple[str, ...]:
    """Validate explicit external manifest URLs, no default public API provider."""
    values = [v.strip() for v in (config or "").split(",") if v.strip()]
    if len(values) > MAX_SOURCES:
        raise ValueError("too many subtitle addon sources")
    result = []
    for raw in values:
        parsed = urlsplit(raw)
        if (parsed.scheme != "https" or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                not parsed.path.endswith("/manifest.json")):
            raise ValueError("subtitle source must be an HTTPS addon manifest")
        prefix = parsed.path[:-len("/manifest.json")].rstrip("/")
        result.append(urlunsplit(("https", parsed.netloc, prefix, "", "")))
    return tuple(dict.fromkeys(result))


def source_id(url: str, language: str, video_id: str) -> str:
    payload = json.dumps([url, language.lower(), video_id], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fetch_bytes(url: str, limit: int) -> bytes:
    """Bounded network fetch with blocked LAN/metadata destinations on every redirect."""
    deadline = upstream.Deadline(20)
    response, connection = upstream.open_url(
        url, "GET", {"user-agent": _UA}, False, deadline
    )
    try:
        if response.status != 200:
            raise ValueError(f"subtitle provider returned HTTP {response.status}")
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("subtitle provider response too large")
        return data
    finally:
        response.close()
        connection.close()


def discover(config: str, media_type: str, video_id: str, extra: str = "") -> list[dict]:
    """Find original subtitles for one film or episode without translating them.

    Each choice retains its original source URL internally, but only a hashed
    opaque ID is advertised to the player. The source is revalidated when
    translation starts so client requests cannot inject arbitrary fetch URLs.
    """
    if media_type not in ("movie", "series", "other") or not _VIDEO.fullmatch(video_id):
        return []
    found: list[dict] = []
    # An extra path is optional and must be encoded as one segment, not a query.
    extra_segment = quote(extra, safe="=&%")[:2048] if extra else ""
    for base in provider_roots(config):
        path = f"/subtitles/{quote(media_type)}/{quote(video_id)}"
        if extra_segment:
            path += "/" + extra_segment
        try:
            data = json.loads(fetch_bytes(base + path + ".json", MAX_JSON))
        except (ValueError, OSError, TimeoutError, UnicodeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("subtitles"), list):
            continue
        for item in data["subtitles"][:40]:
            if not isinstance(item, dict):
                continue
            url, lang = item.get("url"), item.get("lang")
            if not isinstance(url, str) or not isinstance(lang, str):
                continue
            normalized = normalize_language(lang)
            if normalized is None or normalized.split("-")[0] == "pt":
                continue
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname or
                    parsed.username or parsed.password or len(url) > 2048):
                continue
            provider_host = urlsplit(base).hostname or ""
            provider_name = (
                "OpenSubtitles v3" if provider_host == "opensubtitles-v3.strem.io"
                else provider_host[:64] or "Addon de legendas"
            )
            found.append({
                "id": source_id(url, normalized, video_id), "url": url,
                "sourceAddon": provider_name,
                "sourceLanguage": normalized,
                # Use the original subtitle release name when supplied by the
                # provider; a generic "eng" hides which file was selected.
                "name": (str(item.get("subtitleFileName") or item.get("name") or lang)
                         .replace("\n", " ").replace("\r", " "))[:100],
            })
    return list({item["id"]: item for item in found}.values())[:80]
