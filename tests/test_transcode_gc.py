"""Transcode job-directory lifecycle: deletion on stop, orphan sweeping, and containment.

Before this, nothing in the package ever removed a job directory: `/destroy` terminated ffmpeg and
left the whole fMP4 segment tree on disk, and `"transcode"` is in cache.PROTECTED so the evictor was
forbidden from touching it. A single job dir on our own box survived two months and two container
restarts holding 173 files. At `-hls_time 4` a two-hour playback writes ~1800 segments, so the leak
is measured in thousands of files and gigabytes per week of ordinary viewing.
"""
from __future__ import annotations

import os
import time

import pytest

from stremiosrv.transcode.converter import Converter


class FakeProc:
    """Stands in for subprocess.Popen. `alive` drives poll(), as the real handle does."""

    def __init__(self, alive: bool = True):
        self.alive = alive
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False

    def kill(self):
        self.killed = True
        self.alive = False

    def wait(self, timeout=None):
        self.alive = False
        return 0


def _job(conv: Converter, job_id: str, files: int = 3):
    """Materialise a job dir with segment files, the way ffmpeg leaves one behind."""
    d = conv.base / job_id
    d.mkdir(parents=True, exist_ok=True)
    for i in range(files):
        (d / f"seg{i}.m4s").write_bytes(b"x" * 1024)
    (d / "index.m3u8").write_text("#EXTM3U\n")
    return d


def _tracked_workload(
    conv: Converter,
    job_id: str,
    *,
    alive: bool = True,
    touched: bool = True,
):
    """Register a workload using the shared-workload ownership model."""
    import hashlib

    key = hashlib.sha256(
        ("gc-test:" + job_id).encode("utf-8")
    ).hexdigest()

    d = conv._workload_dir(key)
    d.mkdir(parents=True, exist_ok=True)

    for i in range(3):
        (d / f"seg{i}.m4s").write_bytes(b"x" * 1024)

    (d / "index.m3u8").write_text("#EXTM3U\\n")

    proc = FakeProc(alive=alive)

    conv._jobs[key] = proc
    conv._job_workload[job_id] = key
    conv._workload_jobs[key] = {job_id}

    if touched:
        conv._seen[key] = time.monotonic()

    return key, d, proc


def test_stop_removes_the_job_directory(tmp_path):
    conv = Converter(str(tmp_path), None)
    _, d, _ = _tracked_workload(conv, "job1")
    conv.stop("job1")
    assert not d.exists(), "stop() terminated ffmpeg but left its segments on disk"


def test_stop_removes_the_directory_even_with_no_tracked_process(tmp_path):
    """After a restart the process handle is gone but the directory is not. A client calling
    /destroy on a job this process never started must still reclaim the disk."""
    conv = Converter(str(tmp_path), None)
    d = _job(conv, "orphan")
    conv.stop("orphan")
    assert not d.exists()


def test_stop_waits_for_the_child_before_deleting(tmp_path):
    """ffmpeg creates a new file per segment; deleting under a live process races it."""
    conv = Converter(str(tmp_path), None)
    _, d, p = _tracked_workload(conv, "job1")
    conv.stop("job1")
    assert p.terminated and not p.alive
    assert not d.exists()


def test_stop_all_removes_every_job_directory(tmp_path):
    conv = Converter(str(tmp_path), None)
    dirs = [
        _tracked_workload(conv, f"job{i}")[1]
        for i in range(3)
    ]
    conv.stop_all()
    assert not any(d.exists() for d in dirs)


def test_sweep_removes_orphans(tmp_path):
    """Nothing in this process owns these; they belong to a dead one."""
    conv = Converter(str(tmp_path), None)
    dirs = [_job(conv, f"dead{i}") for i in range(3)]
    assert conv.sweep(max_age=0) == 3
    assert not any(d.exists() for d in dirs)


def test_sweep_spares_a_live_job(tmp_path):
    conv = Converter(str(tmp_path), None)
    live = _job(conv, "live")
    dead = _job(conv, "dead")
    conv._jobs["live"] = FakeProc(alive=True)
    assert conv.sweep(max_age=0) == 1
    assert live.exists(), "swept a job whose ffmpeg is still running"
    assert not dead.exists()


def test_sweep_spares_a_job_whose_process_is_registered_but_exited(tmp_path):
    """A finished ffmpeg still owns its segments until the client stops or ages out: playback of a
    fully-transcoded file continues reading them after the process exits."""
    conv = Converter(str(tmp_path), None)
    d = _job(conv, "finished")
    conv._jobs["finished"] = FakeProc(alive=False)
    assert conv.sweep(max_age=3600) == 0
    assert d.exists()


def test_sweep_respects_max_age(tmp_path):
    conv = Converter(str(tmp_path), None)
    d = _job(conv, "recent")
    assert conv.sweep(max_age=3600) == 0
    assert d.exists(), "swept a directory younger than the grace"


def test_sweep_removes_an_orphan_older_than_max_age(tmp_path):
    conv = Converter(str(tmp_path), None)
    d = _job(conv, "stale")
    old = time.time() - 7200
    os.utime(d, (old, old))
    assert conv.sweep(max_age=3600) == 1
    assert not d.exists()


def test_sweep_tolerates_a_missing_transcode_dir(tmp_path):
    """First boot: nothing has transcoded yet."""
    conv = Converter(str(tmp_path), None)
    assert conv.sweep(max_age=0) == 0


def test_sweep_ignores_stray_files_next_to_job_dirs(tmp_path):
    conv = Converter(str(tmp_path), None)
    conv.base.mkdir(parents=True, exist_ok=True)
    stray = conv.base / "notes.txt"
    stray.write_text("hi")
    conv.sweep(max_age=0)
    assert stray.exists(), "sweep deleted something that is not a job directory"


# --- containment -----------------------------------------------------------------------------
# `job_id` comes straight from the URL and is now used to DELETE a tree, so it must not be able to
# name one outside the transcode base. The read path had the same hole: `%2e%2e` survives client-side
# normalisation and reached the route as `..`, so /hlsv2/%2e%2e/certificates.pem served the server's
# TLS private key with a 200.

BACKSLASH = chr(92)  # kept out of the literals below so no escaping hazard survives an edit
BAD_JOB_IDS = [
    "..", ".", "../..", "/etc", "a/b", "",
    ".." + BACKSLASH + "..", "a" + BACKSLASH + "b",
]


@pytest.mark.parametrize("bad", BAD_JOB_IDS)
def test_job_dir_rejects_ids_that_escape_the_base(tmp_path, bad):
    conv = Converter(str(tmp_path), None)
    with pytest.raises(ValueError):
        conv.job_dir(bad)


def test_job_dir_accepts_a_normal_job_id(tmp_path):
    conv = Converter(str(tmp_path), None)
    assert conv.job_dir("c0b2e2ac94952992ea168ebdba147a1a").parent == conv.base


def test_sweep_is_not_fooled_by_an_mtime_from_the_future(tmp_path):
    """`now` is read once before the loop, so a directory stamped even microseconds later has a
    NEGATIVE age -- and `age < max_age` then skips it at max_age=0, the startup sweep that is
    documented as taking no grace. It showed up as an intermittent "2 == 3" in the suite; on a
    real box it means a job directory written moments before startup survives the sweep meant to
    reclaim exactly that.
    """
    conv = Converter(str(tmp_path), None)
    d = _job(conv, "stamped-ahead")
    ahead = time.time() + 5
    os.utime(d, (ahead, ahead))
    assert conv.sweep(max_age=0) == 1
    assert not d.exists()


# --- abandoned transcodes (issue #2) -------------------------------------------------------------
# The sweep spares any job whose ffmpeg is alive, so a transcode nobody is consuming was protected
# BY ITS OWN LIVENESS: a crashed player left ffmpeg at ~99% CPU indefinitely, and repeated attempts
# stacked several at once because each attempt carries a fresh job id. Nothing tracked whether a
# client was still reading the output -- only whether the encoder was running.

def test_a_transcode_nobody_is_reading_is_terminated(tmp_path):
    conv = Converter(str(tmp_path), None)
    key, d, p = _tracked_workload(conv, "abandoned")
    conv._seen[key] -= 600

    assert conv.reap_idle(300) == ["abandoned"]
    assert p.terminated
    assert not d.exists()


def test_a_transcode_someone_is_still_reading_is_left_alone(tmp_path):
    conv = Converter(str(tmp_path), None)
    _, d, p = _tracked_workload(conv, "watched")

    assert conv.reap_idle(300) == []
    assert not p.terminated
    assert d.exists()


def test_a_segment_request_keeps_a_transcode_alive(tmp_path):
    """A segment request refreshes activity for the shared workload."""
    conv = Converter(str(tmp_path), None)
    key, _, _ = _tracked_workload(conv, "job")
    conv._seen[key] -= 600

    conv.touch("job")
    assert conv.reap_idle(300) == []


def test_reaping_detaches_a_job_whose_process_already_exited(tmp_path):
    """An exited encoder is reaped from bookkeeping without terminating it again."""
    conv = Converter(str(tmp_path), None)
    key, _, p = _tracked_workload(
        conv,
        "finished",
        alive=False,
    )
    conv._seen[key] -= 600

    assert conv.reap_idle(300) == ["finished"]
    assert not p.terminated
    assert key not in conv._jobs
    assert not (conv.base / key).exists()


def test_end_waits_after_killing_a_stubborn_encoder(tmp_path):
    """A SIGKILLed ffmpeg child must be waited so it cannot remain defunct."""
    import subprocess

    conv = Converter(str(tmp_path), None)

    class StubbornProc:
        def __init__(self):
            self.wait_calls = 0
            self.killed = False

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            self.wait_calls += 1
            if timeout is not None:
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            return -9

        def kill(self):
            self.killed = True

    proc = StubbornProc()
    conv._end(proc)

    assert proc.killed is True
    assert proc.wait_calls == 2


def test_an_untouched_job_is_reaped_rather_than_living_forever(tmp_path):
    """A workload with no recorded activity must not become immortal."""
    conv = Converter(str(tmp_path), None)
    _, _, p = _tracked_workload(
        conv,
        "unknown",
        touched=False,
    )

    assert conv.reap_idle(0) == ["unknown"]
    assert p.terminated


def test_starting_a_job_counts_as_activity(tmp_path):
    """A newly registered workload counts as recent activity."""
    conv = Converter(str(tmp_path), None)
    _tracked_workload(conv, "fresh")
    assert conv.reap_idle(300) == []


def test_the_gc_loop_is_what_calls_the_reaper(monkeypatch):
    """A reaper nothing invokes is the original bug with more code in it. The loop is its only
    caller, so the wiring gets an assertion of its own."""
    import stremiosrv.transcode.converter as convmod

    reaped, swept = [], []

    class Conv:
        def reap_idle(self, idle_after):
            reaped.append(idle_after)
            return []

        def sweep(self, max_age=0.0):
            swept.append(max_age)
            return 0

    class Stop(Exception):
        pass

    sleeps = []

    def fake_sleep(_):
        sleeps.append(1)
        if len(sleeps) >= 2:
            raise Stop

    monkeypatch.setattr(convmod.time, "sleep", fake_sleep)
    with pytest.raises(Stop):
        convmod.run_transcode_gc(Conv(), interval=1, max_age=600, idle_after=42)

    assert reaped == [42], "the loop never asked the converter to reap idle transcodes"
    assert swept == [600], "the loop stopped sweeping directories"
