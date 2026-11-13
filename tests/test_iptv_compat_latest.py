"""M3U/HLS compatibility policy must never alter native torrent byte-range playback."""
from stremiosrv.api.hls import _iptv_compatibility_decision


def _pr(fmt, duration=0, video="hevc", audio="ac3"):
    return {
        "format": {"name": fmt, "duration": duration},
        "streams": [
            {"track": "video", "codec": video, "width": 3840},
            {"track": "audio", "codec": audio, "channels": 2},
        ],
    }


def test_external_hls_forces_video_audio_compat():
    dec = {"video": {"action": "copy"}, "audio": {"action": "copy"}}
    result, live = _iptv_compatibility_decision(
        dec, _pr("hls"), "https://iptv.example/live/channel.m3u8?token=opaque"
    )
    assert live
    assert result["video"] == {"action": "transcode", "scale_width": 1920}
    assert result["audio"] == {"action": "transcode"}


def test_external_live_mpegts_is_compatible():
    result, live = _iptv_compatibility_decision(
        {"video": {"action": "copy"}, "audio": {"action": "copy"}},
        _pr("mpegts"), "https://iptv.example/live/ch"
    )
    assert live
    assert result["video"]["action"] == "transcode"
    assert result["audio"]["action"] == "transcode"


def test_finite_torrent_mpegts_not_force_transcoded():
    result, live = _iptv_compatibility_decision(
        {"video": {"action": "copy"}, "audio": {"action": "copy"}},
        _pr("mpegts", duration=4500), "https://iptv.example/vod/movie.ts"
    )
    assert not live
    assert result["video"]["action"] == "copy"


def test_internal_torrent_source_is_untouched():
    infohash = "a" * 40
    result, live = _iptv_compatibility_decision(
        {"video": {"action": "copy"}, "audio": {"action": "copy"}},
        _pr("mpegts"), f"http://127.0.0.1:11470/{infohash}/0"
    )
    assert not live
    assert result["video"]["action"] == "copy"


def test_m3u8_detected_even_when_probe_reports_mpegts():
    result, live = _iptv_compatibility_decision(
        {"video": {"action": "copy"}, "audio": {"action": "copy"}},
        _pr("mpegts", 1200), "https://iptv.example/edge/channel.m3u8"
    )
    assert live
    assert result["audio"]["action"] == "transcode"



def test_external_hls_probe_is_accepted_but_torrent_manifest_is_rejected():
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from stremiosrv.app import create_app

    playlist_probe = _pr("hls")
    c = TestClient(create_app())
    with (
        patch("stremiosrv.api.hls.resolve_media_input", side_effect=lambda r, url: url),
        patch("stremiosrv.api.hls.probe_media", return_value=playlist_probe),
    ):
        r = c.get("/hlsv2/probe", params={"mediaURL": "https://iptv.example/channel.m3u8"})
        assert r.status_code == 200
        r = c.get(
            "/hlsv2/probe", params={"mediaURL": f"http://127.0.0.1:11470/{'a' * 40}/0"}
        )
        assert r.status_code == 415


def test_external_hls_master_runs_compatible_transcoding(tmp_path):
    """The endpoint really hands H.264/AAC and live HLS policy to Converter."""
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from stremiosrv.app import create_app

    master = tmp_path / "master.m3u8"
    master.write_text(
        "#EXTM3U\n#EXT-X-VERSION:7\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000\nindex.m3u8\n"
    )

    class ConverterFake:
        def __init__(self):
            self.decisions = []

        def ensure_job(self, job_id, media, decision, **kw):
            self.decisions.append((job_id, media, decision))
            return tmp_path

        def workload_for_job(self, job_id):
            return "iptv-encoder-test"

    app = create_app()
    fake = ConverterFake()
    app.state.converter = fake
    channel = "https://iptv.example/watch/live.m3u8"
    with (
        patch("stremiosrv.api.hls.resolve_media_input", return_value="http://127.0.0.1:11470/reader-ticket"),
        patch("stremiosrv.api.hls.probe_media", return_value=_pr("hls", video="h264", audio="aac")),
    ):
        response = TestClient(app).get(
            "/hlsv2/test-iptv-job/master.m3u8",
            params={"mediaURL": channel, "videoCodecs": "h264", "audioCodecs": "aac"},
        )
    assert response.status_code == 200, response.text
    assert response.text.startswith("#EXTM3U")
    job_id, resolved, decision = fake.decisions[0]
    assert job_id == "test-iptv-job"
    assert resolved.endswith("/reader-ticket")
    assert decision["video"]["action"] == "transcode"
    assert decision["audio"]["action"] == "transcode"
    assert decision["_liveInput"] is True
