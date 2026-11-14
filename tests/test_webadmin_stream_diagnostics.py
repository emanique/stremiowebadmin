from pathlib import Path
import sys
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webadmin"))
import app

HASH = "a" * 40

def test_live_metrics_without_peer_ips(monkeypatch):
    def fake_get(path, fallback):
        assert path == f"/{HASH}/stats.json"
        return {"infoHash": HASH, "name": "Movie.mkv", "peers": 34,
                "downloadSpeed": 1048576, "uploadSpeed": 2048,
                "downloaded": 450000000, "files": [{"name":"Movie.mkv"}],
                "wires": [{"ip":"192.0.2.4", "port":6881}]}
    monkeypatch.setattr(app, "get_json", fake_get)
    out = app.stream_diagnostics(HASH.upper())
    assert out["peers"] == 34 and out["filesWithMetadata"] == 1
    assert "wires" not in out and "ip" not in str(out)

def test_invalid_and_absent(monkeypatch):
    with pytest.raises(HTTPException) as invalid:
        app.stream_diagnostics("../bad")
    assert invalid.value.status_code == 400
    monkeypatch.setattr(app, "get_json", lambda path, fallback: None)
    with pytest.raises(HTTPException) as unavailable:
        app.stream_diagnostics(HASH)
    assert unavailable.value.status_code == 503
    monkeypatch.setattr(app, "get_json", lambda path, fallback: {"infoHash":"b"*40})
    with pytest.raises(HTTPException) as absent:
        app.stream_diagnostics(HASH)
    assert absent.value.status_code == 404

def test_dashboard_button():
    html = (Path(__file__).resolve().parents[1] / "webadmin/static/index.html").read_text()
    assert "data-stream-diagnostics" in html
    assert "metrics.peers" in html
