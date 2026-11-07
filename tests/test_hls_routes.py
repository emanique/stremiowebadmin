from unittest.mock import patch

import pytest

from fastapi.testclient import TestClient

from stremiosrv.app import create_app


def test_hwaccel_profiler_default_none():
    c = TestClient(create_app())
    r = c.get("/hwaccel-profiler")
    assert r.status_code == 200
    assert r.json()["profile"] is None


def test_master_503_without_converter():
    c = TestClient(create_app())
    r = c.get("/hlsv2/abc/master.m3u8", params={"mediaURL": "http://x/0"})
    assert r.status_code == 503


def test_segment_503_without_converter():
    c = TestClient(create_app())
    r = c.get("/hlsv2/abc/seg0.m4s")
    assert r.status_code == 503


def test_non_int_idx_does_not_match_serve():
    # regression: paths like /hlsv2/probe must NOT be swallowed by playback's serve route.
    # Before pinning idx to :int this returned 422 (int_parsing); now it 404s and falls through.
    c = TestClient(create_app())
    r = c.get("/somehash/notanint")
    assert r.status_code == 404


# --- HEAD parity. FastAPI does not add HEAD to a GET route (bare Starlette does), so every hlsv2
# route answered 405 while the byte-range route answered 206. A client that probes a URL with HEAD
# before playing it saw a hard failure on the transcode path only.


def _app_client(monkeypatch):
    from fastapi.testclient import TestClient

    from stremiosrv.api import hls
    from stremiosrv.app import create_app

    monkeypatch.setattr(hls, "probe_media", lambda url: {"format": {"name": "matroska"}, "streams": []})
    monkeypatch.setattr(hls, "resolve_media_input", lambda request, url: url)
    return TestClient(create_app())


def test_head_is_accepted_on_probe(monkeypatch):
    c = _app_client(monkeypatch)
    r = c.head("/hlsv2/probe", params={"mediaURL": "http://x/y"})
    assert r.status_code == 200
    assert r.content == b""  # HEAD carries headers only


def test_head_matches_get_headers_on_probe(monkeypatch):
    """Same headers as GET is the actual contract — a HEAD that lies about content-type is worse
    than a 405, because the client believes it."""
    c = _app_client(monkeypatch)
    g = c.get("/hlsv2/probe", params={"mediaURL": "http://x/y"})
    h = c.head("/hlsv2/probe", params={"mediaURL": "http://x/y"})
    assert g.status_code == h.status_code == 200
    assert h.headers.get("content-type") == g.headers.get("content-type")


def test_head_is_accepted_on_the_playlist_and_segment_routes(monkeypatch):
    """Both reach their handler rather than the router's 405: no transcoder is wired in this app,
    so 503 is the handler talking. What matters is that it is not 405."""
    c = _app_client(monkeypatch)
    for path in ("/hlsv2/job1/master.m3u8", "/hlsv2/job1/video0.m4s"):
        r = c.head(path, params={"mediaURL": "http://x/y"})
        assert r.status_code != 405, path


class _FakeConv:
    def __init__(self):
        self.stopped = []
        self.touched = []

    def stop(self, job_id):
        self.stopped.append(job_id)

    def touch(self, job_id):
        self.touched.append(job_id)

    def job_dir(self, job_id):
        import pathlib

        return pathlib.Path("/nonexistent")

    def job_file(self, job_id, filename):
        return self.job_dir(job_id) / filename


def test_asking_for_a_segment_marks_the_job_as_still_being_watched(tmp_path):
    """The reaper ends transcodes nothing has read, and this route is where "read" is observed. Its
    absence is not visible in any test of the reaper itself: that would keep passing while the
    server quietly killed the encoder of a player that was watching perfectly happily.

    Real files on disk, because a missing one makes the route wait 35 seconds for a segment that is
    never coming -- correct behaviour, and a minute of it does not belong in the suite."""
    from fastapi.testclient import TestClient

    from stremiosrv.app import create_app

    (tmp_path / "seg7.m4s").write_bytes(b"x")
    (tmp_path / "index.m3u8").write_text("#EXTM3U\n")

    class Served(_FakeConv):
        def job_file(self, job_id, filename):
            return tmp_path / filename

    app = create_app()
    conv = Served()
    app.state.converter = conv
    c = TestClient(app)

    assert c.get("/hlsv2/job1/seg7.m4s").status_code == 200
    assert conv.touched == ["job1"]

    assert c.get("/hlsv2/job1/index.m3u8").status_code == 200
    assert conv.touched == ["job1", "job1"]


def test_a_malformed_job_path_is_refused_before_it_counts_as_activity():
    """Otherwise anyone who can reach the route could keep a transcode alive with nonsense ids."""
    from fastapi.testclient import TestClient

    from stremiosrv.app import create_app

    class Rejecting(_FakeConv):
        def job_file(self, job_id, filename):
            raise ValueError("unsafe")

    app = create_app()
    conv = Rejecting()
    app.state.converter = conv
    c = TestClient(app)

    assert c.get("/hlsv2/job1/..%2Fescape").status_code in (400, 404)
    assert conv.touched == []


def test_head_cannot_tear_down_a_transcode_job():
    """HEAD is defined as safe, and /destroy is the one route here whose GET has a side effect. It
    is left off the HEAD list, so a HEAD falls through to the segment route and 404s instead —
    what matters is that stop() is never reached. Asserted as behaviour, not as a status code: the
    catch-all /{job_id}/{filename} also matches this path, so the code alone would not prove it."""
    from fastapi.testclient import TestClient

    from stremiosrv.app import create_app

    app = create_app()
    conv = _FakeConv()
    app.state.converter = conv
    c = TestClient(app)

    c.head("/hlsv2/job1/destroy")
    assert conv.stopped == []          # HEAD must not destroy anything

    assert c.get("/hlsv2/job1/destroy").status_code == 200
    assert conv.stopped == ["job1"]    # ...while GET still does


# --- a probe that never answers. Both routes here need it before they can do anything, so both
# answer 504 -- the gateway timeout the playlist route already uses when a transcode won't start.


def _times_out(url):
    from stremiosrv.transcode.probe import ProbeTimeoutError

    raise ProbeTimeoutError("ffprobe did not answer within 30s")


def test_probe_answers_504_when_ffprobe_times_out(monkeypatch):
    from stremiosrv.api import hls

    monkeypatch.setattr(hls, "probe_media", _times_out)
    monkeypatch.setattr(hls, "resolve_media_input", lambda request, url: url)
    c = TestClient(create_app())
    assert c.get("/hlsv2/probe", params={"mediaURL": "http://x/y"}).status_code == 504


def test_the_master_playlist_answers_504_when_ffprobe_times_out(monkeypatch):
    """With a converter attached, so the 503 for a missing one cannot stand in for the answer."""
    from stremiosrv.api import hls

    monkeypatch.setattr(hls, "probe_media", _times_out)
    monkeypatch.setattr(hls, "resolve_media_input", lambda request, url: url)
    app = create_app()
    app.state.converter = _FakeConv()
    r = TestClient(app).get("/hlsv2/job1/master.m3u8", params={"mediaURL": "http://x/y"})
    assert r.status_code == 504


def test_master_advertises_embedded_subtitles_without_ffmpeg_subtitle_muxing():
    from stremiosrv.api.hls import _master_with_subtitles

    master = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:7\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=422400,CODECS="avc1.640828,mp4a.40.2"\n'
        "index.m3u8\n"
    )
    probe = {
        "format": {"duration": 6621.455},
        "streams": [
            {"index": 0, "track": "video", "codec": "hevc"},
            {"index": 1, "track": "audio", "codec": "aac", "lang": "eng"},
            {"index": 31, "track": "subtitle", "codec": "subrip", "lang": "por", "title": "Forced", "forced": True},
            {"index": 32, "track": "subtitle", "codec": "ass", "lang": "por", "title": "Regular Full"},
            {"index": 33, "track": "subtitle", "codec": "subrip", "lang": "eng", "title": "SDH", "hearingImpaired": True},
        ],
    }
    media = "https://host/" + "a" * 40 + "/0?"
    out = _master_with_subtitles(master, probe, media)

    assert out.count("#EXT-X-MEDIA:TYPE=SUBTITLES") == 3
    assert 'GROUP-ID="subs"' in out
    assert 'LANGUAGE="por"' in out
    assert 'LANGUAGE="eng"' in out
    assert 'NAME="por — Forced"' in out
    assert 'NAME="por — Regular Full"' in out
    assert 'NAME="eng — SDH"' in out
    assert 'FORCED=YES' in out
    assert "subtitles/31.m3u8?" in out
    assert "subtitles/32.m3u8?" in out
    assert "subtitles/33.m3u8?" in out
    assert 'SUBTITLES="subs"' in out
    # Regression for 2.0.13: no FFmpeg var_stream_map subtitle-only metadata such as sname appears.
    assert "sname:" not in out


def test_subtitle_media_playlist_points_to_global_track_vtt():
    from stremiosrv.api.hls import _subtitle_media_playlist

    info_hash = "b" * 40
    media = f"https://host/{info_hash}/0?"
    out = _subtitle_media_playlist(media, 31, 6621.455)

    assert out.startswith("#EXTM3U")
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in out
    assert "#EXT-X-MEDIA-SEQUENCE:0" in out
    assert "#EXT-X-TARGETDURATION:30" in out
    assert "#EXTINF:30.000," in out
    assert "#EXTINF:21.455," in out
    assert out.count("#EXTINF:") == 221
    assert f"/{info_hash}/0/subtitles.vtt?" in out
    assert "track=31" in out
    assert "start=0.000" in out
    assert "start=6600.000" in out
    assert "duration=30.000" in out
    assert "duration=21.455" in out
    assert "#EXT-X-ENDLIST" in out


def test_subtitle_media_playlist_preserves_guessed_file_index():
    """stremio-core uses /<infohash>/-1 when fileIdx is implicit.

    HLS subtitle discovery must preserve that public URL contract instead
    of rejecting the mediaURL before playback can resolve GuessFileIdx.
    """
    from stremiosrv.api.hls import _subtitle_media_playlist

    info_hash = "d" * 40
    media = f"https://host/{info_hash}/-1?"

    out = _subtitle_media_playlist(media, 2, 90.0)

    assert out.startswith("#EXTM3U")
    assert out.count("#EXTINF:") == 3
    assert f"/{info_hash}/-1/subtitles.vtt?" in out
    assert "track=2" in out
    assert "start=0.000" in out
    assert "start=60.000" in out
    assert "#EXT-X-ENDLIST" in out


def test_subtitle_media_playlist_rejects_non_server_media_url():
    import pytest
    from fastapi import HTTPException
    from stremiosrv.api.hls import _subtitle_media_playlist

    with pytest.raises(HTTPException) as exc:
        _subtitle_media_playlist("https://example.invalid/movie.mkv", 31, 10)
    assert exc.value.status_code == 400


def test_subtitle_playlist_route_marks_job_active(monkeypatch):
    from stremiosrv.api import hls
    from stremiosrv.app import create_app

    info_hash = "c" * 40
    app = create_app()
    conv = _FakeConv()
    app.state.converter = conv
    c = TestClient(app)

    r = c.get(
        "/hlsv2/job1/subtitles/31.m3u8",
        params={"mediaURL": f"https://host/{info_hash}/0?", "duration": 42},
    )
    assert r.status_code == 200
    assert "text" not in r.headers.get("content-type", "")
    assert "#EXTM3U" in r.text
    assert "track=31" in r.text
    assert conv.touched == ["job1"]


# --- Task 7: resolve_media_input wired in, protocol whitelist, HLS-format refusal (Minor 8) ---


def test_probe_resolves_the_media_url():
    """probe_media must receive the RESOLVED URL, never the raw client mediaURL. Asserting only
    that resolve_media_input was called (`m.called`) is vacuous: a regression back to
    `probe_media(mediaURL)` -- the raw value, the exact SSRF bug this task closes -- would still
    call resolve_media_input (its return value would just be discarded) and this test would keep
    passing. The sentinel + assert_called_once_with proves probe_media got THAT value."""
    c = TestClient(create_app())
    resolved = "http://127.0.0.1:11470/RESOLVED"
    with patch("stremiosrv.api.hls.resolve_media_input", return_value=resolved), \
         patch("stremiosrv.api.hls.probe_media",
               return_value={"format": {"name": "matroska"}, "streams": [], "samples": {}}) as pm:
        c.get("/hlsv2/probe", params={"mediaURL": "https://cdn.example/v.mkv"})
    pm.assert_called_once_with(resolved)  # not the raw client URL


def test_master_refuses_hls_format_input():
    """A torrent whose bytes are themselves an HLS playlist must not be handed to ffmpeg as a
    transcode input (Minor 8).

    A converter is attached deliberately: create_app() with none wired in answers 503
    ("transcoder unavailable") before master() ever reaches the probe/refusal, which would make
    this test pass vacuously through the 503 branch instead of proving the 415 path is reached.
    """
    app = create_app()
    app.state.converter = _FakeConv()
    c = TestClient(app)
    with patch("stremiosrv.api.hls.resolve_media_input", side_effect=lambda r, u: u), \
         patch("stremiosrv.api.hls.probe_media",
               return_value={"format": {"name": "hls"}, "streams": [], "samples": {}}):
        r = c.get("/hlsv2/00000000/master.m3u8",
                  params={"mediaURL": "http://127.0.0.1:11470/aabb/0"})
    assert r.status_code == 415

# --- transient torrent-head readiness -------------------------------------------------------------

def test_hls_media_resolve_retries_one_readiness_504(monkeypatch):
    from fastapi import HTTPException
    from stremiosrv.api import hls

    calls = []

    def resolve(request, url):
        calls.append(url)
        if len(calls) == 1:
            raise HTTPException(status_code=504, detail="media source too slow")
        return "http://safe/resolved"

    monkeypatch.setattr(hls, "resolve_media_input", resolve)
    monkeypatch.setattr(hls.time, "sleep", lambda _: None)
    assert hls._resolve_media_for_hls(object(), "http://source") == "http://safe/resolved"
    assert calls == ["http://source", "http://source"]


def test_hls_media_resolve_keeps_second_readiness_504(monkeypatch):
    from fastapi import HTTPException
    from stremiosrv.api import hls

    calls = []

    def resolve(request, url):
        calls.append(url)
        raise HTTPException(status_code=504, detail="media source too slow")

    monkeypatch.setattr(hls, "resolve_media_input", resolve)
    monkeypatch.setattr(hls.time, "sleep", lambda _: None)
    with pytest.raises(HTTPException) as exc:
        hls._resolve_media_for_hls(object(), "http://source")
    assert exc.value.status_code == 504
    assert calls == ["http://source", "http://source"]


@pytest.mark.parametrize("status,detail", [
    (403, "media source not allowed"),
    (415, "playlist inputs are not accepted"),
    (504, "probe timed out"),
])
def test_hls_media_resolve_never_retries_other_failures(monkeypatch, status, detail):
    from fastapi import HTTPException
    from stremiosrv.api import hls

    calls = []

    def resolve(request, url):
        calls.append(url)
        raise HTTPException(status_code=status, detail=detail)

    monkeypatch.setattr(hls, "resolve_media_input", resolve)
    monkeypatch.setattr(hls.time, "sleep", lambda _: None)
    with pytest.raises(HTTPException) as exc:
        hls._resolve_media_for_hls(object(), "http://source")
    assert exc.value.status_code == status
    assert calls == ["http://source"]


def test_torrent_input_pacing_only_when_current_wanted_data_is_incomplete():
    from types import SimpleNamespace
    from stremiosrv.api import hls

    class Handle:
        def __init__(self, finished):
            self.finished = finished
        def is_finished(self):
            return self.finished

    class Engine:
        def __init__(self, handle):
            self.handle = handle
        def get(self, info_hash):
            return self.handle

    url = "https://127.0.0.1:12470/" + ("a" * 40) + "/0"
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=Engine(Handle(False)))))
    assert hls._torrent_input_is_incomplete(request, url) is True
    request.app.state.engine = Engine(Handle(True))
    assert hls._torrent_input_is_incomplete(request, url) is False

def test_subtitle_media_playlist_propagates_mpegts_start():
    from stremiosrv.api import hls as hls_api

    info_hash = "a" * 40
    media = f"https://host/{info_hash}/0?"
    body = hls_api._subtitle_media_playlist(media, 10, 60.0, 129750)

    assert "mpegtsStart=129750" in body
    assert body.count("mpegtsStart=129750") == 2


def test_hls_resume_propagates_offset_to_embedded_subtitle_playlist():
    from stremiosrv.api.hls import _master_with_subtitles, _subtitle_media_playlist

    info_hash = "e" * 40
    media = f"https://host/{info_hash}/1?"
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=8000000\nindex.m3u8\n"
    probe = {
        "format": {"duration": 7220.768},
        "streams": [{"index": 2, "track": "subtitle", "codec": "subrip", "lang": "eng"}],
    }
    out = _master_with_subtitles(master, probe, media, 674804)
    assert "startTime=674804" in out
    assert "duration=6545.964" in out

    playlist = _subtitle_media_playlist(media, 2, 6545.964, 0, 674804)
    assert "start=674.804" in playlist
    assert "timelineOffset=674.804" in playlist
    assert "start=704.804" in playlist


def test_webvtt_timestamp_map_can_rebase_resume_offset():
    from stremiosrv.api.subs import _add_webvtt_timestamp_map

    payload = b"WEBVTT\n\n00:11:15.000 --> 00:11:17.000\nHello\n"
    out = _add_webvtt_timestamp_map(payload, 674.804, 0, 674.804)
    assert b"X-TIMESTAMP-MAP=LOCAL:00:11:14.804,MPEGTS:0" in out
