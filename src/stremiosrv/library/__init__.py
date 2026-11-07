"""Library catalog presentation helpers.

Two metadata paths coexist deliberately:

* authoritative labels learned from Stremio playback or created by the Library page;
* display-only recognition for old/unlabelled cache entries whose release name can be matched with
  high confidence against Cinemeta.

The second path improves the My Library catalog (title + artwork) without silently turning a filename
guess into an authoritative stream association. Playback learning remains the source of truth used
when the addon answers a normal Cinemeta ``tt...`` stream request.
"""
from __future__ import annotations

import re

from stremiosrv.library import addon_model as _addon_model
from stremiosrv.library import metadata as _metadata

_METAHUB_POSTER = "https://images.metahub.space/poster/medium/{}/img"
_IMDB_ID = re.compile(r"^tt[0-9]+$")
_ORIGINAL_PREVIEW = _addon_model.preview
_ORIGINAL_META_FOR = _addon_model.meta_for


def _display_entry(entry: dict) -> dict:
    """Return ``entry`` with best-effort display metadata, never mutating library state."""
    existing = entry.get("label") or {}
    release_name = str(entry.get("wantedFile") or entry.get("name") or "")

    # Complete labels already came from an explicit user/download action and win outright.
    if existing.get("name") and (existing.get("poster") or existing.get("metaId")):
        return entry

    recognised = _metadata.resolve(release_name)
    if not recognised:
        return entry

    # If playback already taught us an IMDb id, filename recognition may only enrich it when it
    # agrees with that identity. Never replace an authoritative learned id with a filename guess.
    if existing.get("metaId") and recognised.get("metaId") != existing.get("metaId"):
        return entry

    merged = {**recognised, **existing}
    # Fill blanks from recognised metadata while preserving every non-empty authoritative value.
    for key, value in recognised.items():
        if not merged.get(key):
            merged[key] = value
    return {**entry, "label": merged}


def _poster_for(entry: dict) -> str:
    label = entry.get("label") or {}
    explicit = str(label.get("poster") or "").strip()
    if explicit:
        return explicit
    meta_id = str(label.get("metaId") or "").split(":", 1)[0]
    return _METAHUB_POSTER.format(meta_id) if _IMDB_ID.fullmatch(meta_id) else ""


def _episode_suffix(label: dict) -> str:
    season = label.get("season")
    episode = label.get("episode")
    if isinstance(season, int) and isinstance(episode, int):
        return f" · S{season:02d}E{episode:02d}"
    if isinstance(season, int):
        return f" · S{season:02d}"
    return ""


def _preview_with_poster(entry: dict) -> dict:
    shown = _display_entry(entry)
    item = _ORIGINAL_PREVIEW(shown)
    label = shown.get("label") or {}
    suffix = _episode_suffix(label)
    if suffix and suffix not in str(item.get("name") or ""):
        item["name"] = str(item.get("name") or "") + suffix
    poster = _poster_for(shown)
    if poster:
        item.setdefault("poster", poster)
        item.setdefault("posterShape", "poster")
    return item


def _meta_with_poster(entry: dict) -> dict:
    shown = _display_entry(entry)
    item = _ORIGINAL_META_FOR(shown)
    label = shown.get("label") or {}
    suffix = _episode_suffix(label)
    if suffix and suffix not in str(item.get("name") or ""):
        item["name"] = str(item.get("name") or "") + suffix
    poster = _poster_for(shown)
    if poster:
        item.setdefault("poster", poster)
        item.setdefault("posterShape", "poster")
    return item


# addon.py calls these functions through addon_model. Keep matching/playback logic untouched; only
# the catalog/meta presentation boundary receives best-effort recognition.
_addon_model.preview = _preview_with_poster
_addon_model.meta_for = _meta_with_poster
