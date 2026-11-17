"""Only complete, indexed, unhandled torrents qualify for offline AI work."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from stremiosrv.ai_subtitles.resume_scan import complete_cached_files


class FakeFiles:
    def file_path(self, index): return "film.mkv"
    def file_size(self, index): return 1024
    def file_offset(self, index): return 0


class FakeTorrent:
    def __init__(self, ih): self.ih = ih
    def info_hashes(self): return SimpleNamespace(v1=self.ih)
    def num_files(self): return 1
    def num_pieces(self): return 1
    def piece_length(self): return 2048
    def files(self): return FakeFiles()


def _setup(tmp_path, *, flags=True, ti_hash=None):
    ih = "a" * 40
    resume_dir = tmp_path / ".resume"
    resume_dir.mkdir()
    (resume_dir / (ih + ".fastresume")).write_bytes(b"resume")
    (tmp_path / "film.mkv").write_bytes(b"x" * 1024)
    record = SimpleNamespace(
        ti=FakeTorrent(ti_hash or ih),
        have_pieces=[flags],
    )
    return ih, record


def _scan(tmp_path, ih, record, *, active=()):
    with (
        patch("stremiosrv.ai_subtitles.resume_scan.cache.load_name_index",
              return_value={"film.mkv": ih}),
        patch("libtorrent.read_resume_data", return_value=record),
    ):
        return list(complete_cached_files(tmp_path, set(active)))


def test_full_resume_detected(tmp_path):
    ih, record = _setup(tmp_path)
    result = _scan(tmp_path, ih, record)
    assert len(result) == 1
    assert result[0][0:2] == (ih, 0)
    assert result[0][2].name == "film.mkv"


def test_partial_resume_not_accepted(tmp_path):
    ih, record = _setup(tmp_path, flags=False)
    assert _scan(tmp_path, ih, record) == []


def test_wrong_infohash_not_accepted(tmp_path):
    ih, record = _setup(tmp_path, ti_hash="b" * 40)
    assert _scan(tmp_path, ih, record) == []


def test_active_handle_takes_precedence(tmp_path):
    ih, record = _setup(tmp_path)
    assert _scan(tmp_path, ih, record, active=(ih,)) == []


def test_missing_file_not_accepted(tmp_path):
    ih, record = _setup(tmp_path)
    (tmp_path / "film.mkv").unlink()
    assert _scan(tmp_path, ih, record) == []


def test_symlink_not_accepted(tmp_path):
    ih, record = _setup(tmp_path)
    (tmp_path / "film.mkv").unlink()
    (tmp_path / "other.mkv").write_bytes(b"x" * 1024)
    (tmp_path / "film.mkv").symlink_to(tmp_path / "other.mkv")
    assert _scan(tmp_path, ih, record) == []
