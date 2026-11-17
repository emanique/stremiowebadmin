"""Discover complete torrent files unloaded from the libtorrent session.

A cache index proves torrent identity, NOT completion. The saved libtorrent
resume bitfield, metadata hash, exact file length and non-sparse allocation
must all agree before the AI worker can transcribe a cached file. No torrent
is opened, reactivated or modified by this scanner.
"""
from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Iterator

from stremiosrv import cache
from stremiosrv.torrent.picker import pieces_for_range

logger = logging.getLogger(__name__)
_HASH = re.compile(r"^[a-f0-9]{40}$")
_SUPPORTED = frozenset({".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v", ".ts"})


def complete_cached_files(root: Path, active_hashes: set[str], *, limit: int = 80
                          ) -> Iterator[tuple[str, int, Path, int]]:
    """Yield (infohash, file_index, media_path, torrent_file_count).

    This is a conservative fastresume-backed check, not a full piece-hash
    recheck. Refuse partial, sparse, symlinked or unexpectedly changed data.
    A live libtorrent handle always takes precedence over a saved snapshot.
    """
    import libtorrent as lt

    root = Path(root).resolve()
    resume_dir = root / ".resume"
    if not resume_dir.is_dir() or resume_dir.is_symlink():
        return
    try:
        indexed = cache.load_name_index(str(root))
    except (OSError, ValueError, TypeError):
        return
    seen: set[tuple[str, int]] = set()
    for count, (indexed_name, hash_value) in enumerate(indexed.items()):
        if count >= limit:
            break
        if not isinstance(indexed_name, str) or not isinstance(hash_value, str):
            continue
        infohash = hash_value.lower()
        if not _HASH.fullmatch(infohash) or infohash in active_hashes:
            continue
        name_path = Path(indexed_name)
        if (not indexed_name or name_path.is_absolute() or len(name_path.parts) != 1
                or indexed_name.startswith(".") or ".." in name_path.parts):
            continue
        record = resume_dir / (infohash + ".fastresume")
        try:
            if record.is_symlink() or not record.is_file() or record.stat().st_size > 8 * 1024 * 1024:
                continue
            resume = lt.read_resume_data(record.read_bytes())
            ti = resume.ti
            if ti is None or str(ti.info_hashes().v1).lower() != infohash:
                continue
            piece_flags = list(resume.have_pieces)
            if len(piece_flags) < ti.num_pieces():
                continue
            file_set = ti.files()
            total_files = ti.num_files()
            if total_files < 1 or total_files > 1000:
                continue
            for index in range(total_files):
                relative = Path(file_set.file_path(index))
                if (relative.is_absolute() or ".." in relative.parts
                        or relative.suffix.lower() not in _SUPPORTED):
                    continue
                if relative.parts[0] != indexed_name:
                    continue
                length = file_set.file_size(index)
                if length <= 0:
                    continue
                offset = file_set.file_offset(index)
                end = offset + length - 1
                if not all(p < len(piece_flags) and piece_flags[p]
                           for p in pieces_for_range(offset, end, ti.piece_length())):
                    continue
                raw_media = root / relative
                if raw_media.is_symlink():
                    continue
                media = raw_media.resolve()
                if not media.is_relative_to(root):
                    continue
                st = media.stat()
                if st.st_size != length:
                    continue
                # No sparse file may be inferred complete from a stale resume.
                # Some compressed filesystems may be rejected conservatively.
                allocated = getattr(st, "st_blocks", None)
                if allocated is not None and allocated * 512 < length:
                    continue
                if (infohash, index) not in seen:
                    seen.add((infohash, index))
                    yield infohash, index, media, total_files
        except (OSError, RuntimeError, ValueError, IndexError, TypeError) as exc:
            logger.debug("Ignoring unverifiable offline cache entry %s: %s", infohash[:12], exc)
