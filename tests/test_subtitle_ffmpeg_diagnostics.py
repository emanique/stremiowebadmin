"""Sanitised subtitle failures without blocking the streaming subprocess."""
from __future__ import annotations

import logging
import tempfile

from stremiosrv.api.subs import (
    _close_subtitle_diagnostics,
    _read_webvtt_window,
    _subtitle_ffmpeg_failure_reason,
    _webvtt_stream,
)


class _FailedProcess:
    def __init__(self, text: bytes):
        import io
        self.stdout = io.BytesIO(b"")
        self._subtitle_diagnostic_file = tempfile.TemporaryFile(mode="w+b")
        self._subtitle_diagnostic_file.write(text)
        self.returncode = 1

    def communicate(self, timeout=None):
        return (b"", None)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def test_failed_subtitle_error_is_categorized_without_leaking_secrets(caplog):
    url = b"https://somehost.example/private.mkv?token=SECRET_DO_NOT_LOG"
    proc = _FailedProcess(b"Invalid data found when processing input: " + url)
    with caplog.at_level(logging.WARNING):
        result = _read_webvtt_window(proc, 10.0, 3.0)
    assert result == b""
    assert "reason=incomplete_or_invalid_media" in caplog.text
    assert "SECRET_DO_NOT_LOG" not in caplog.text
    assert proc._subtitle_diagnostic_file.closed


def test_unrecognised_error_does_not_log_raw_stderr(caplog):
    proc = _FailedProcess(b"viewer-private-identifier and token=secret")
    with caplog.at_level(logging.WARNING):
        assert b"".join(_webvtt_stream(proc)) == b""
    assert "reason=unclassified" in caplog.text
    assert "viewer-private-identifier" not in caplog.text
    assert proc._subtitle_diagnostic_file.closed


def test_error_classifier_without_diagnostic_sink():
    assert _subtitle_ffmpeg_failure_reason(object()) == "unclassified"


def test_diagnostics_cleanup_is_safe_for_test_doubles():
    _close_subtitle_diagnostics(object())
