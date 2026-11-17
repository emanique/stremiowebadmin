"""The UI shows measured whisper.cpp inference progress, not elapsed-time ETA."""
import sys

import pytest

from stremiosrv.ai_subtitles.generator import run_whisper_with_progress
from stremiosrv.ai_subtitles.service import AutoSubtitleWorker


def test_whisper_progress_is_streamed_and_monotonic():
    code = (
        "import sys\n"
        "for value in (5, 30, 29, 83, 125):\n"
        " sys.stderr.write(f'whisper_print_progress_callback: progress = {value}%\\n')\n"
        " sys.stderr.flush()\n"
    )
    progress = []
    run_whisper_with_progress([sys.executable, "-c", code], progress.append)
    assert progress == [5, 30, 83, 99]


def test_progress_is_only_exposed_while_transcribing(tmp_path):
    worker = AutoSubtitleWorker(None, str(tmp_path))
    worker._active = {"status": "queued", "_started": 0.0}
    worker._set_stage("transcribing")
    worker._set_progress(43)
    worker._set_progress(31)
    worker._set_progress(109)
    assert worker.snapshot()["active"]["progressPercent"] == 99
    worker._set_stage("translating")
    assert "progressPercent" not in worker.snapshot()["active"]


def test_nonzero_whisper_result_fails():
    code = "import sys;sys.stderr.write('progress = 15%\\n');sys.exit(7)"
    from subprocess import CalledProcessError
    with pytest.raises(CalledProcessError):
        run_whisper_with_progress([sys.executable, "-c", code], lambda _: None)
