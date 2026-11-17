"""Regression: the current main cache purger must clean AI subtitles by hash."""
from stremiosrv import cache


def test_full_hash_purge_cleans_ai_subtitles_only_for_target(tmp_path):
    target = "a" * 40
    unrelated = "b" * 40
    (tmp_path / "myfilm.mkv").write_bytes(b"media")
    for infohash in (target, unrelated):
        directory = tmp_path / ".ai-subtitles" / infohash
        directory.mkdir(parents=True)
        (directory / "test.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nBom dia.\n", encoding="utf-8"
        )

    result = cache.purge_title(str(tmp_path), target, names=("myfilm.mkv",))
    assert result["hash"] == target
    assert not (tmp_path / "myfilm.mkv").exists()
    assert not (tmp_path / ".ai-subtitles" / target).exists()
    assert (tmp_path / ".ai-subtitles" / unrelated / "test.srt").exists()
