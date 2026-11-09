from pathlib import Path
DASHBOARD = (Path(__file__).resolve().parents[1] / "webadmin/static/transcoding-dashboard.js").read_text()

def test_live_ffmpeg_not_hidden_if_playback_registry_empty():
    assert "const liveSessions = sessions;" in DASHBOARD
    assert "const unmatchedSessions = registryAvailable" in DASHBOARD
    assert "unregistered playback job(s)" in DASHBOARD

def test_copy_session_and_unregistered_status_preserved():
    assert "session.action === 'direct-stream'" in DASHBOARD
    assert "UNREGISTERED · " in DASHBOARD
    assert "No active FFmpeg processes" in DASHBOARD
