"""Regression for selection-bound AI: never scan all torrents."""
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from stremiosrv.ai_subtitles.on_demand import (
    DemandSubtitleSessions, _absolute_srt, _audio_available, LEASE_SECONDS
)


class FakeHandle:
    def has_metadata(self): return True
    def num_files(self): return 1
    def file_path(self, index): return "movie.mkv"
    def file_complete(self, index): return True
    def file_offset(self, index): return 0
    def file_size(self, index): return 1024


def test_absolute_time_for_new_srt_cue():
    srt = "1\n00:00:01,500 --> 00:00:04,000\nOlá.\n"
    shifted = _absolute_srt(srt, 5100)
    assert "01:25:01,500 --> 01:25:04,000" in shifted


def test_missing_audio_is_not_considered_complete(tmp_path):
    fake = FakeHandle()
    fake.file_complete = lambda _: False
    with patch("stremiosrv.ai_subtitles.on_demand.subprocess.run",
               side_effect=OSError("unavailable stream")):
        assert not _audio_available(fake, tmp_path / "missing.mkv", 0, 0)


def test_selection_notifies_status_and_expiry_without_library_scan(tmp_path):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"test bytes")
    fake = FakeHandle()
    engine = SimpleNamespace(get=lambda ih: fake)
    now = [100.0]
    sess = DemandSubtitleSessions(engine, str(tmp_path), clock=lambda: now[0])
    try:
        with patch.object(sess, "_schedule") as scheduled:
            entry = sess.start("a" * 40, 0, 42.5)
            assert entry["positionSeconds"] == 42.5
            scheduled.assert_called_once()
            sid = entry["sessionId"]
            assert sess.heartbeat(sid, 5400)["positionSeconds"] == 5400
            assert sess.status(sid)["stage"] == "preparing"
            assert sess.snapshot()["automaticScan"] is False
            now[0] += LEASE_SECONDS + 1
            assert sess._active(sid) is None
            with pytest.raises(KeyError):
                sess.status("invalid")
    finally:
        sess.close()


def test_stop_does_not_delete_cache(tmp_path):
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"untouched")
    sess = DemandSubtitleSessions(SimpleNamespace(get=lambda _: FakeHandle()), str(tmp_path))
    try:
        with patch.object(sess, "_schedule"):
            row = sess.start("b" * 40, 0, 10)
        sess.stop(row["sessionId"])
        assert movie.read_bytes() == b"untouched"
        with pytest.raises(KeyError):
            sess.status(row["sessionId"])
    finally:
        sess.close()


def test_invalid_position_rejected(tmp_path):
    sess = DemandSubtitleSessions(None, str(tmp_path))
    try:
        with pytest.raises(ValueError):
            sess.start("a" * 40, 0, float("nan"))
        with pytest.raises(ValueError):
            sess.start("a" * 40, 0, -1)
        assert sess.snapshot()["active"] == []
    finally:
        sess.close()


def test_partial_piece_verification_requires_every_packet(tmp_path):
    fake = FakeHandle()
    fake.file_complete = lambda _: False
    fake.piece_length = lambda: 1024
    fake.have_piece = lambda i: i != 1
    import json
    packets = [{"pos": i * 100, "size": 90, "pts_time": str(i)} for i in range(28)]
    packets[14]["pos"] = 1024
    with patch("stremiosrv.ai_subtitles.on_demand.subprocess.run",
               return_value=SimpleNamespace(stdout=json.dumps({"packets": packets}))):
        assert not _audio_available(fake, tmp_path / "movie.mkv", 0, 0)


def test_hash_purge_includes_progressive_subtitles_but_not_other_titles(tmp_path):
    from stremiosrv.ai_subtitles.generator import delete_hash
    target = "a" * 40
    other = "b" * 40
    folder = tmp_path / ".ai-subtitles" / target / "on-demand" / "0"
    folder.mkdir(parents=True)
    (folder / "chunk-00000150.srt").write_text("caption")
    untouched = tmp_path / ".ai-subtitles" / other / "on-demand" / "0"
    untouched.mkdir(parents=True)
    (untouched / "chunk-00000150.srt").write_text("preserve")
    assert delete_hash(tmp_path, target) == 1
    assert not folder.exists()
    assert (untouched / "chunk-00000150.srt").exists()


def test_dormant_complete_torrent_uses_verified_fastresume(tmp_path):
    from stremiosrv.ai_subtitles.on_demand import DemandSubtitleSessions
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"complete")
    ih = "c" * 40
    worker = DemandSubtitleSessions(SimpleNamespace(get=lambda key: None), str(tmp_path))
    try:
        with patch("stremiosrv.ai_subtitles.resume_scan.complete_cached_files",
                   return_value=iter([(ih, 0, media, 1)])):
            handle, verified_media = worker._assert_media(ih, 0)
        assert handle is None and verified_media == media
    finally:
        worker.close()


def test_dormant_unverified_torrent_is_not_accepted(tmp_path):
    from stremiosrv.ai_subtitles.generator import GenerationUnavailable
    worker = DemandSubtitleSessions(SimpleNamespace(get=lambda key: None), str(tmp_path))
    try:
        with patch("stremiosrv.ai_subtitles.resume_scan.complete_cached_files",
                   return_value=iter([])):
            with pytest.raises(GenerationUnavailable, match="verified complete cache"):
                worker._assert_media("c" * 40, 0)
    finally:
        worker.close()


def test_gpu_prefetches_two_blocks_ahead_without_library_scan(tmp_path):
    from stremiosrv.ai_subtitles.on_demand import LOOKAHEAD_BLOCKS, CHUNK_SECONDS
    assert CHUNK_SECONDS == 30
    assert LOOKAHEAD_BLOCKS == 2
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"complete movie")
    worker = DemandSubtitleSessions(SimpleNamespace(get=lambda _: FakeHandle()), str(tmp_path))
    try:
        with patch.object(worker, "_schedule"):
            session = worker.start("a" * 40, 0, 1920)
        sid = session["sessionId"]
        def row():
            return worker._sessions[sid]
        folder = worker._folder(row())
        folder.mkdir(parents=True)
        assert worker._pending_block(row()) == 1920
        (folder / "chunk-00001920.srt").write_text("1\n00:32:00,000 --> 00:32:20,000\nLegenda\n")
        assert worker._pending_block(row()) == 1950
        (folder / "chunk-00001950.srt").write_text("Legenda")
        assert worker._pending_block(row()) == 1980
        (folder / "chunk-00001980.srt").write_text("Legenda")
        assert worker._pending_block(row()) is None
        state = worker.status(sid)
        assert state["currentBlockReady"]
        assert state["bufferedUntilSeconds"] == 2010
    finally:
        worker.close()


def test_normal_playback_does_not_cancel_inference_on_block_boundary(tmp_path):
    worker = DemandSubtitleSessions(
        SimpleNamespace(get=lambda _: FakeHandle()), str(tmp_path))
    (tmp_path / "movie.mkv").write_bytes(b"cached video")
    try:
        with patch.object(worker, "_schedule"):
            sid = worker.start("b" * 40, 0, 1925)["sessionId"]
            worker._running = sid
            worker._running_block = 1950
            with patch.object(worker, "_cancel_bridge") as cancel:
                worker.heartbeat(sid, 1930)
                worker.heartbeat(sid, 1951)
                worker.heartbeat(sid, 1956)
                cancel.assert_not_called()
                assert not worker._sessions[sid].get("seekPending", False)
    finally:
        worker._running = None
        worker.close()


def test_far_seek_cancels_obsolete_block_only(tmp_path):
    worker = DemandSubtitleSessions(
        SimpleNamespace(get=lambda _: FakeHandle()), str(tmp_path))
    (tmp_path / "movie.mkv").write_bytes(b"cached video")
    try:
        with patch.object(worker, "_schedule"):
            sid = worker.start("b" * 40, 0, 1920)["sessionId"]
            worker._running = sid
            worker._running_block = 1950
            worker._bridge_job = "ai-subtitles-sample123"
            with patch.object(worker, "_cancel_bridge") as cancel:
                worker.heartbeat(sid, 3000)
                cancel.assert_called_once_with("ai-subtitles-sample123")
                assert worker._sessions[sid]["seekPending"] is True
    finally:
        worker._running = None
        worker._bridge_job = None
        worker.close()


def test_progressive_srt_is_one_stable_atomic_file(tmp_path):
    from stremiosrv.ai_subtitles.on_demand import DemandSubtitleSessions
    folder = tmp_path / ".ai-subtitles" / ("a"*40) / "on-demand" / "0" / "0"
    folder.mkdir(parents=True)
    (folder / "chunk-00000000.srt").write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nPrimeiro.\n", encoding="utf8")
    target = DemandSubtitleSessions._publish_progressive(folder)
    assert target.name == "progressive.srt"
    assert "Primeiro." in target.read_text("utf8")
    (folder / "chunk-00000030.srt").write_text(
        "1\n00:00:31,000 --> 00:00:33,000\nSegundo.\n", encoding="utf8")
    assert DemandSubtitleSessions._publish_progressive(folder) == target
    data = target.read_text("utf8")
    assert data.count(" --> ") == 2
    assert "Primeiro." in data and "Segundo." in data
    assert data.index("Primeiro.") < data.index("Segundo.")
    assert data.startswith("1\n")
    assert "\n\n2\n" in data


def test_repeated_gpu_failure_does_not_spin(tmp_path):
    from stremiosrv.ai_subtitles.on_demand import DemandSubtitleSessions, MAX_BLOCK_FAILURES
    (tmp_path / "movie.mkv").write_bytes(b"video")
    obj = DemandSubtitleSessions(SimpleNamespace(get=lambda _: FakeHandle()), str(tmp_path))
    try:
        with patch.object(obj, "_schedule"):
            row = obj.start("d"*40, 0, 0)
        sid = row["sessionId"]
        with obj._lock:
            for _ in range(MAX_BLOCK_FAILURES):
                obj._note_chunk_failure(obj._sessions[sid], 0)
        assert obj._pending_block(obj._sessions[sid]) is None
    finally:
        obj.close()


def test_client_diagnostics_are_bounded_and_redacted(tmp_path):
    obj = DemandSubtitleSessions(None, str(tmp_path), clock=lambda: 100.0)
    try:
        for n in range(28):
            obj.record_client_event("selected" if n % 2 else "started", 200)
        snapshot = obj.snapshot()
        assert len(snapshot["recentClientEvents"]) == 20
        assert all(set(item) == {"event", "httpStatus", "secondsAgo"}
                   for item in snapshot["recentClientEvents"])
        with pytest.raises(ValueError):
            obj.record_client_event("some-secret-token-here")
    finally:
        obj.close()


def test_ai_to_pt_never_uses_embedded_subtitles(tmp_path):
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"video")
    obj = DemandSubtitleSessions(SimpleNamespace(get=lambda _: FakeHandle()), str(tmp_path))
    try:
        with patch.object(obj, "_schedule"), patch(
                "stremiosrv.subtitle_translator.addon._embedded_tracks",
                side_effect=AssertionError("AI to PT must not inspect subtitle tracks")):
            row = obj.start("a" * 40, 0, 12)
        assert "embeddedSubtitleStream" not in row
        assert "on-demand" in obj._folder(row).parts
        assert "on-demand-timed" not in obj._folder(row).parts
    finally:
        obj.close()


def test_ai_to_pt_does_not_offer_srt_translation_shortcut():
    import inspect
    source = inspect.getsource(DemandSubtitleSessions._process_next)
    assert "_embedded_window" not in source
    assert "run_openvino_bridge(scratch" in source
    assert "json_to_srt(" in source


def test_reject_millisecond_position_above_known_movie_duration(tmp_path):
    with patch.object(DemandSubtitleSessions, "_assert_media",
                      return_value=(None, tmp_path / "movie.mkv")), patch.object(
                      DemandSubtitleSessions, "_duration_seconds", return_value=6766.334):
        obj = DemandSubtitleSessions(None, str(tmp_path))
        try:
            with pytest.raises(ValueError, match="exceeds media duration"):
                obj.start("a" * 40, 0, 72430)
        finally:
            obj.close()


def test_out_of_range_heartbeat_stops_existing_session(tmp_path):
    obj = DemandSubtitleSessions(None, str(tmp_path))
    try:
        sid = "qa-clock-wrong-unit"
        obj._sessions[sid] = dict(
            sessionId=sid, infohash="a" * 40, fileIndex=0, audioIndex=0,
            durationSeconds=6766.334, positionSeconds=1200, lastSeen=obj.clock())
        with pytest.raises(ValueError, match="exceeds media duration"):
            obj.heartbeat(sid, 72430)
        assert sid not in obj._sessions
    finally:
        obj.close()


def test_second_player_does_not_evict_first_session(tmp_path):
    (tmp_path / "movie.mkv").write_bytes(b"video")
    worker = DemandSubtitleSessions(
        SimpleNamespace(get=lambda _: FakeHandle()), str(tmp_path))
    try:
        with patch.object(worker, "_schedule"):
            first = worker.start("a" * 40, 0, 120)
            second = worker.start("a" * 40, 0, 2400)
        assert first["sessionId"] != second["sessionId"]
        assert worker.status(first["sessionId"])["positionSeconds"] == 120
        assert worker.status(second["sessionId"])["positionSeconds"] == 2400
        assert len(worker.snapshot()["active"]) == 2
        worker.stop(first["sessionId"])
        assert worker.status(second["sessionId"])["positionSeconds"] == 2400
        assert len(worker.snapshot()["active"]) == 1
    finally:
        worker.close()


def test_two_players_share_chunk_cache_without_sharing_playhead(tmp_path):
    (tmp_path / "movie.mkv").write_bytes(b"video")
    worker = DemandSubtitleSessions(
        SimpleNamespace(get=lambda _: FakeHandle()), str(tmp_path))
    try:
        with patch.object(worker, "_schedule"):
            a = worker.start("b" * 40, 0, 180)
            b = worker.start("b" * 40, 0, 1920)
        first = worker._sessions[a["sessionId"]]
        second = worker._sessions[b["sessionId"]]
        assert worker._folder(first) == worker._folder(second)
        assert worker._pending_block(first) == 180
        assert worker._pending_block(second) == 1920
        folder = worker._folder(first)
        folder.mkdir(parents=True)
        (folder / "chunk-00000180.srt").write_text("cached block")
        assert worker._pending_block(first) == 210
        assert worker._pending_block(second) == 1920
        worker.stop(a["sessionId"])
        assert (folder / "chunk-00000180.srt").exists()
    finally:
        worker.close()


def test_native_sessions_receive_distinct_srt_windows(tmp_path):
    (tmp_path / "movie.mkv").write_bytes(b"video")
    worker = DemandSubtitleSessions(
        SimpleNamespace(get=lambda _: FakeHandle()), str(tmp_path))
    try:
        with patch.object(worker, "_schedule"):
            a = worker.start("a"*40, 0, 60)
            b = worker.start("a"*40, 0, 3000)
        folder = worker._folder(worker._sessions[a["sessionId"]])
        folder.mkdir(parents=True)
        (folder / "chunk-00000060.srt").write_text(
            "1\n00:01:01,000 --> 00:01:03,000\nPlayer A\n")
        (folder / "chunk-00003000.srt").write_text(
            "1\n00:50:01,000 --> 00:50:03,000\nPlayer B\n")
        assert "Player A" in worker.session_srt(a["sessionId"])
        assert "Player B" not in worker.session_srt(a["sessionId"])
        assert "Player B" in worker.session_srt(b["sessionId"])
        assert "Player A" not in worker.session_srt(b["sessionId"])
        worker.stop(a["sessionId"])
        with pytest.raises(KeyError):
            worker.session_srt(a["sessionId"])
        assert "Player B" in worker.session_srt(b["sessionId"])
    finally:
        worker.close()


def test_simultaneous_players_share_failure_budget_without_eviction(tmp_path):
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"video")
    tick = [100.0]
    worker = DemandSubtitleSessions(
        SimpleNamespace(get=lambda _: FakeHandle()), str(tmp_path), clock=lambda: tick[0])
    try:
        with patch.object(worker, "_schedule"):
            first = worker.start("e" * 40, 0, 1020)
            second = worker.start("e" * 40, 0, 1020)
            third = worker.start("e" * 40, 0, 1920)
        a, b, c = (x["sessionId"] for x in (first, second, third))
        assert len(worker.snapshot()["active"]) == 3
        with worker._lock:
            assert worker._note_chunk_failure(worker._sessions[a], 1020) == 1
            assert worker._pending_block(worker._sessions[b]) is None
            assert worker._pending_block(worker._sessions[c]) == 1920
            tick[0] += 46.0
            assert worker._pending_block(worker._sessions[b]) == 1020
            assert worker._note_chunk_failure(worker._sessions[b], 1020) == 2
            assert worker._pending_block(worker._sessions[a]) is None
            assert worker._pending_block(worker._sessions[b]) is None
            assert worker._pending_block(worker._sessions[c]) == 1920
        worker.stop(a)
        assert len(worker.snapshot()["active"]) == 2
        assert worker.status(b)["positionSeconds"] == 1020
        assert worker.status(c)["positionSeconds"] == 1920
    finally:
        worker.close()


def test_shared_cached_block_bypasses_previous_openvino_failure(tmp_path):
    (tmp_path / "movie.mkv").write_bytes(b"movie")
    worker = DemandSubtitleSessions(SimpleNamespace(get=lambda _: FakeHandle()), str(tmp_path))
    try:
        with patch.object(worker, "_schedule"):
            sid = worker.start("f" * 40, 0, 0)["sessionId"]
        row = worker._sessions[sid]
        with worker._lock:
            worker._note_chunk_failure(row, 0)
            worker._note_chunk_failure(row, 0)
        assert worker._pending_block(row) is None
        folder = worker._folder(row)
        folder.mkdir(parents=True)
        (folder / "chunk-00000000.srt").write_text(
            "1\n00:00:01,000 --> 00:00:03,000\nBloco recuperado\n")
        assert worker._pending_block(row) == 30
        assert worker.session_srt(sid).count("Bloco recuperado") == 1
    finally:
        worker.close()
