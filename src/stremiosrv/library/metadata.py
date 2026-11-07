"""Best-effort metadata recognition for unlabeled cached torrents.

This module deliberately accepts only high-confidence matches. A wrong IMDb identity is worse than
no artwork because it would make the Library addon display misleading metadata.

Recognition flow:
1. parse a release/torrent name into a conservative title/year/episode hint;
2. query the official Cinemeta search catalog for the inferred content type;
3. accept only an exact normalised title match, with year agreement when a title year is present;
4. return a small label payload suitable for display or later persistence.

The resolver is network-optional. Failures and ambiguous searches simply return None.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from functools import lru_cache

CINEMETA_ROOT = "https://v3-cinemeta.strem.io"
_REQUEST_TIMEOUT = 3

_VIDEO_EXT_RE = re.compile(r"\.(?:mkv|mp4|avi|m4v|mov|ts|webm|m2ts)$", re.I)
_EPISODE_RE = re.compile(r"\bS(\d{1,2})[ ._-]*E(\d{1,3})\b", re.I)
_SEASON_RE = re.compile(r"\bS(\d{1,2})\b", re.I)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_TECH_RE = re.compile(
    r"\b(?:2160p|1080p|720p|480p|WEB[- .]?DL|WEBRip|BluRay|BRRip|HDRip|HDTV|NF\b|AMZN\b|"
    r"HEVC|x26[45]|H\.?26[45]|AV1|DDP?\b|AAC\b|DTS\b|HDR\b|DV\b|REMUX\b|MULTI\b|PROPER\b|"
    r"REPACK\b|EXTENDED\b|UNCUT\b)\b",
    re.I,
)
_BRACKET_RE = re.compile(r"\[[^\]]*\]|\([^)]*(?:torrent|yts|eztv|rarbg)[^)]*\)", re.I)


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def release_hint(name: str) -> dict | None:
    """Extract a conservative metadata search hint from a torrent or video file name."""
    raw = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    raw = _VIDEO_EXT_RE.sub("", raw)
    raw = _BRACKET_RE.sub(" ", raw).replace("_", ".")

    episode = _EPISODE_RE.search(raw)
    season_only = _SEASON_RE.search(raw)
    year_match = _YEAR_RE.search(raw)
    type_ = "series" if episode or season_only else "movie"

    cut_positions: list[int] = []
    for match in (episode, season_only, _TECH_RE.search(raw)):
        if match:
            cut_positions.append(match.start())
    if year_match:
        before_year = re.sub(r"[.\-_]+", " ", raw[: year_match.start()]).strip()
        # A leading numeric title such as "1923.S02E01" must keep its title. Otherwise a year
        # before the season/episode marker is a useful title boundary (e.g. Show.2024.S02E01).
        if len(_norm(before_year)) >= 2:
            cut_positions.append(year_match.start())

    title_part = raw[: min(cut_positions)] if cut_positions else raw
    title = re.sub(r"[.\-]+", " ", title_part)
    title = re.sub(r"\s+", " ", title).strip(" .-_()[]")
    if len(_norm(title)) < 2:
        return None

    hint: dict[str, object] = {"title": title, "type": type_}

    # For a series, a year AFTER Sxx/Exx normally describes that season/release, not the show's
    # original year. A title that itself IS a year (e.g. "1923") is not a year constraint either.
    marker_start = episode.start() if episode else (season_only.start() if season_only else None)
    year_is_title = bool(year_match and _norm(title) == year_match.group(1))
    if (year_match and not year_is_title
            and (type_ == "movie" or marker_start is None or year_match.start() < marker_start)):
        hint["year"] = int(year_match.group(1))

    if episode:
        hint["season"] = int(episode.group(1))
        hint["episode"] = int(episode.group(2))
    elif season_only:
        hint["season"] = int(season_only.group(1))
    return hint


def _candidate_year(meta: dict) -> int | None:
    for key in ("year", "releaseInfo"):
        value = meta.get(key)
        if isinstance(value, int):
            return value
        match = _YEAR_RE.search(str(value or ""))
        if match:
            return int(match.group(1))
    return None


def _search_url(type_: str, title: str) -> str:
    query = urllib.parse.quote(title, safe="")
    return f"{CINEMETA_ROOT}/catalog/{type_}/top/search={query}.json"


@lru_cache(maxsize=256)
def _search(type_: str, title: str) -> tuple[dict, ...]:
    try:
        req = urllib.request.Request(
            _search_url(type_, title),
            headers={"User-Agent": "stremiosrv-library-metadata/1"},
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as response:
            body = json.loads(response.read(1024 * 1024).decode("utf-8"))
    except Exception:
        return ()
    metas = body.get("metas") if isinstance(body, dict) else None
    return tuple(item for item in (metas or []) if isinstance(item, dict))


def resolve(name: str) -> dict | None:
    """Return a metadata label, or None when recognition is not certain."""
    hint = release_hint(name)
    if not hint:
        return None
    title = str(hint["title"])
    type_ = str(hint["type"])
    wanted = _norm(title)
    year = hint.get("year")

    exact = [m for m in _search(type_, title)
             if isinstance(m.get("id"), str) and re.fullmatch(r"tt\d+", m["id"])
             and _norm(str(m.get("name") or "")) == wanted]
    if year is not None:
        year_matches = [m for m in exact if _candidate_year(m) == year]
        if year_matches:
            exact = year_matches
        elif any(_candidate_year(m) is not None for m in exact):
            return None
    if len({m.get("id") for m in exact}) != 1:
        return None

    meta = exact[0]
    label: dict[str, object] = {
        "metaId": meta["id"],
        "type": type_,
        "name": str(meta.get("name") or title),
    }
    poster = str(meta.get("poster") or "").strip()
    if poster:
        label["poster"] = poster
    if "season" in hint:
        label["season"] = hint["season"]
    if "episode" in hint:
        label["episode"] = hint["episode"]
        label["videoId"] = f"{meta['id']}:{hint['season']}:{hint['episode']}"
    return label
