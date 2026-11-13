from pathlib import Path
from stremiosrv.playback_sessions import PlaybackRegistry


def test_external_hls_has_no_torrent_identity_but_has_playback_record():
    registry = PlaybackRegistry(clock=lambda: 100.0)
    sid = registry.register_hls_job("iptv-job", "", -1, "Mozilla/5.0", "encoder-workload")
    registry.touch_hls("iptv-job", "Mozilla/5.0")
    rows = registry.snapshot()["active"]
    assert len(rows) == 1
    assert rows[0]["sessionId"] == sid
    assert rows[0]["jobId"] == "iptv-job"
    assert rows[0]["workloadId"] == "encoder-workload"
    assert rows[0]["infoHash"] == ""
    assert rows[0]["fileIdx"] == -1
    registry.end_hls("iptv-job")
    assert registry.snapshot()["active"] == []


def test_external_hls_route_has_unconditional_registry_creation():
    route = (Path(__file__).resolve().parents[1] / "src/stremiosrv/api/hls.py").read_text()
    assert "if registry is not None:" in route
    assert 'parsed_source[0] if parsed_source is not None else ""' in route
    assert "if registry is not None and parsed_source is not None:" not in route


def test_gc_wires_playback_cleanup():
    app = (Path(__file__).resolve().parents[1] / "src/stremiosrv/app.py").read_text()
    assert '"on_reaped": application.state.playback_registry.end_hls' in app


def test_external_media_master_registers_real_hls_session(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from stremiosrv.app import create_app
    from stremiosrv.api import hls

    (tmp_path / "master.m3u8").write_text(
        '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=500000,CODECS="avc1.42e01e,mp4a.40.2"\nindex.m3u8\n'
    )

    class FakeConverter:
        def ensure_job(self, job_id, media, decision, **kwargs):
            return tmp_path

        def workload_for_job(self, job_id):
            return "workload-external"

    monkeypatch.setattr(hls, "_resolve_media_for_hls", lambda request, url: url)
    monkeypatch.setattr(hls, "probe_media", lambda media: {
        "format": {"name": "mpegts", "duration": 0},
        "streams": [{"index": 0, "track": "video", "codec": "h264"},
                    {"index": 1, "track": "audio", "codec": "aac"}],
    })
    app = create_app(converter=FakeConverter())
    response = TestClient(app).get("/hlsv2/external-job/master.m3u8",
        params={"mediaURL": "https://example.org/live-channel.ts"},
        headers={"user-agent": "Mozilla/5.0"})
    assert response.status_code == 200, response.text
    active = app.state.playback_registry.snapshot()["active"]
    assert len(active) == 1
    assert active[0]["jobId"] == "external-job"
    assert active[0]["workloadId"] == "workload-external"
    assert active[0]["infoHash"] == ""
