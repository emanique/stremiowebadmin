import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from stremiosrv.api.subs import parse_stream_url
from stremiosrv.app import create_app


def test_parse_stream_url():
    info_hash = "a" * 40

    assert parse_stream_url(
        f"https://h:12470/{info_hash}/6?"
    ) == (info_hash, 6)

    # stremio-core uses -1 when no explicit fileIdx was supplied.
    # Preserve it; playback resolves -1 through GuessFileIdx.
    assert parse_stream_url(
        f"https://h:12470/{info_hash}/-1?"
    ) == (info_hash, -1)

    assert parse_stream_url("/tmp/movie.mkv") is None
    assert parse_stream_url(f"https://h/{info_hash}/abc") is None
    assert parse_stream_url(f"https://h/{info_hash}/--1") is None
    assert parse_stream_url(f"https://h/{info_hash}/1.5") is None


def test_opensub_hash_null_for_unresolvable_url():
    # a stream URL with no engine -> {"error": null, "result": null}, NOT a 500
    c = TestClient(create_app())
    r = c.get("/opensubHash", params={"videoUrl": "https://h:12470/" + "a" * 40 + "/6"})
    assert r.status_code == 200
    assert r.json() == {"error": None, "result": None}


def test_opensub_hash_requires_source():
    c = TestClient(create_app())
    r = c.get("/opensubHash")
    assert r.status_code == 422


def test_opensubhash_does_not_probe_arbitrary_local_paths(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"x" * 1000)
    c = TestClient(create_app())
    r = c.get("/opensubHash", params={"videoUrl": str(secret)})
    assert r.status_code == 200
    assert r.json() == {"error": None, "result": None}  # never a size/hash for a local path


def test_opensubhash_rejects_a_bare_existing_directory():
    c = TestClient(create_app())
    r = c.get("/opensubHash", params={"videoUrl": os.getcwd()})
    assert r.json() == {"error": None, "result": None}


def test_opensub_hash_returns_size_and_hash_on_engine_success(monkeypatch):
    # The engine-success envelope: a resolvable own stream URL, metadata present and the edge pieces
    # in, returns {"error": null, "result": {"size", "hash"}} -- the shape the OpenSubtitles addon
    # needs (moviehash AND moviebytesize). The null cases are covered above; this covers the hit.
    from stremiosrv.api import subs

    class FakeHandle:
        def has_metadata(self):
            return True

    class FakeEngine:
        def get(self, info_hash):
            return FakeHandle()

        def add(self, info_hash):
            return FakeHandle()

        def save_path(self):
            return "/data"

    monkeypatch.setattr(subs, "_ensure_edges", lambda *a, **k: True)
    monkeypatch.setattr(subs, "file_disk_path", lambda *a, **k: "/data/movie.mkv")
    monkeypatch.setattr(subs, "opensubtitles_hash_and_size", lambda path: ("deadbeefdeadbeef", 4242))
    app = create_app()
    app.state.engine = FakeEngine()
    r = TestClient(app).get("/opensubHash", params={"videoUrl": "https://h:12470/" + "a" * 40 + "/6"})
    assert r.status_code == 200
    assert r.json() == {"error": None, "result": {"size": 4242, "hash": "deadbeefdeadbeef"}}


def test_casting_returns_empty_list():
    c = TestClient(create_app())
    r = c.get("/casting")
    assert r.status_code == 200
    assert r.json() == []


# --- /subtitleSignature: stremio-video >= 0.0.93 calls this at every load whose probe does not rule
# out an embedded subtitle track. We answer the envelope with a null signature on purpose — the
# reference server.js v4.21.1 has no such route (404) and nothing upstream consumes the value, so
# there is no algorithm to implement and a made-up string would be *used* the day a consumer ships.


def test_subtitle_signature_envelope():
    """The exact shape stremio-video parses: resp.error falsy, resp.result.signature present."""
    c = TestClient(create_app())
    r = c.get("/subtitleSignature", params={"videoUrl": "http://x/y"})
    assert r.status_code == 200
    b = r.json()
    assert b["error"] is None
    assert b["result"] == {"signature": None}


def test_subtitle_signature_maps_to_null_in_the_client():
    """Mirror of fetchEmbeddedSubtitleSignature's own expression, so the contract is asserted the
    way the client evaluates it rather than the way we happen to serialise it."""
    c = TestClient(create_app())
    b = c.get("/subtitleSignature", params={"videoUrl": "http://x/y"}).json()
    signature = b["result"]["signature"] if b.get("result") and isinstance(
        b["result"].get("signature"), str) else None
    assert signature is None


def test_subtitle_signature_accepts_the_container_hint():
    c = TestClient(create_app())
    r = c.get("/subtitleSignature", params={"videoUrl": "http://x/y", "container": "matroska,webm"})
    assert r.status_code == 200


def test_subtitle_signature_requires_video_url():
    c = TestClient(create_app())
    assert c.get("/subtitleSignature").status_code == 422


def test_subtitle_signature_never_probes():
    """The reason it is cheap. probe_media() shells out to ffprobe uncached, and this is called at
    playback start on the box that is serving the stream."""
    import stremiosrv.api.subs as subs_api

    calls = []
    original = subs_api.probe_media
    subs_api.probe_media = lambda *a, **k: calls.append(a) or {"format": {}, "streams": []}
    try:
        TestClient(create_app()).get("/subtitleSignature", params={"videoUrl": "http://x/y"})
    finally:
        subs_api.probe_media = original
    assert calls == []


def test_subtitle_signature_is_counted():
    from stremiosrv import metrics

    metrics.reset()
    c = TestClient(create_app())
    c.get("/subtitleSignature", params={"videoUrl": "http://x/y"})
    c.get("/subtitleSignature", params={"videoUrl": "http://x/z"})
    c.get("/subtitleSignature")  # 422, not an ask we could answer
    assert metrics.playback_stats()["subtitleSignatureAsks"] == 2
    metrics.reset()


def test_subtitle_signature_does_not_shadow_other_routes():
    """One-segment literal, registered in the same router as /subtitles.{ext} and after the
    /{info_hash}/... routes. Assert the neighbours still resolve to themselves."""
    c = TestClient(create_app())
    assert c.get("/subtitleSignature", params={"videoUrl": "http://x/y"}).json()["result"] == {
        "signature": None}
    # /subtitles.srt still reaches the proxy route (422 = its own validation, not a 404/mismatch)
    assert c.get("/subtitles.srt").status_code in (422, 400)
    assert c.get("/opensubHash").status_code == 422


def test_subtitles_list_answers_its_empty_shape_when_the_probe_times_out(monkeypatch, caplog):
    """The player asks for this on every playback and it has an ordinary answer for "no tracks",
    so a slow ffprobe must not make it a 500. It is still logged: an empty list for a file that
    does have subtitles is otherwise a silent wrong answer."""
    import logging

    from stremiosrv.api import subs as subs_api
    from stremiosrv.transcode.probe import ProbeTimeoutError

    def _times_out(url):
        raise ProbeTimeoutError("ffprobe did not answer within 30s")

    monkeypatch.setattr(subs_api, "probe_media", _times_out)
    monkeypatch.setattr(subs_api, "resolve_media_input", lambda request, url: url)
    c = TestClient(create_app())
    with caplog.at_level(logging.WARNING):
        r = c.get("/" + "a" * 40 + "/0/subtitles.json", params={"mediaURL": "http://x/y"})
    assert r.status_code == 200
    assert r.json() == {"subtitles": []}
    assert "timed out" in caplog.text


def test_subtitles_vtt_uses_same_global_track_id_as_subtitles_list(monkeypatch):
    import io
    from stremiosrv.api import subs as subs_api

    # This regression starts after upstream 1.6.20 media resolution.
    # Destination/media guard behaviour has dedicated tests.
    monkeypatch.setattr(
        subs_api,
        "resolve_media_input",
        lambda request, url: url,
    )

    monkeypatch.setattr(
        subs_api,
        "probe_media",
        lambda url: {
            "format": {"name": "matroska,webm", "duration": 10},
            "streams": [
                {"id": 0, "index": 0, "track": "video", "codec": "hevc"},
                {"id": 1, "index": 1, "track": "audio", "codec": "aac", "lang": "eng"},
                {"id": 31, "index": 31, "track": "subtitle", "codec": "subrip", "lang": "por"},
            ],
            "samples": {},
        },
    )

    calls = []

    class _Proc:
        def __init__(self, argv, **kwargs):
            calls.append((argv, kwargs))
            self.stdout = io.BytesIO(
                b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nOla.\n"
            )
            self._returncode = None

        def wait(self, timeout=None):
            self._returncode = 0
            return 0

        def poll(self):
            return self._returncode

        def terminate(self):
            self._returncode = 0

        def kill(self):
            self._returncode = -9

    monkeypatch.setattr(subs_api.subprocess, "Popen", _Proc)

    c = TestClient(create_app())
    listed = c.get("/" + "a" * 40 + "/0/subtitles.json", params={"mediaURL": "http://media"})
    assert listed.status_code == 200
    assert listed.json()["subtitles"] == [
        {"id": 31, "track": 31, "codec": "subrip", "lang": "por"}
    ]

    extracted = c.get(
        "/" + "a" * 40 + "/0/subtitles.vtt",
        params={"mediaURL": "http://media", "track": 31},
    )
    assert extracted.status_code == 200
    assert extracted.text.startswith("WEBVTT")
    argv, kwargs = calls[0]
    assert ["-map", "0:31"] == argv[argv.index("-map"):argv.index("-map") + 2]
    assert "0:s:31" not in argv
    assert kwargs["bufsize"] == 0


def test_subtitles_vtt_window_passes_seek_and_duration_to_ffmpeg(monkeypatch):
    import io
    from stremiosrv.api import subs as subs_api

    seen = {}

    monkeypatch.setattr(subs_api, "resolve_media_input", lambda request, url: "http://127.0.0.1/media")
    monkeypatch.setattr(
        subs_api, "probe_media",
        lambda url: {"format": {}, "streams": [
            {"index": 9, "track": "subtitle", "codec": "subrip", "lang": "por"}
        ], "samples": {}},
    )

    class _Proc:
        def __init__(self, argv, **kwargs):
            seen["argv"] = argv
            self.stdout = io.BytesIO(b"WEBVTT\\n\\n")
            self._returncode = None
        def wait(self, timeout=None):
            self._returncode = 0
            return 0
        def poll(self):
            return self._returncode
        def terminate(self):
            self._returncode = 0
        def kill(self):
            self._returncode = -9

    monkeypatch.setattr(subs_api.subprocess, "Popen", _Proc)
    r = TestClient(create_app()).get(
        "/" + "a" * 40 + "/0/subtitles.vtt",
        params={"mediaURL": "http://media", "track": 9, "start": 60, "duration": 30},
    )
    assert r.status_code == 200
    argv = seen["argv"]
    ss_positions = [i for i, value in enumerate(argv) if value == "-ss"]
    assert len(ss_positions) == 1
    assert argv[ss_positions[0]:ss_positions[0] + 2] == ["-ss", "55.000"]
    assert ["-to", "90.000"] == argv[argv.index("-to"):argv.index("-to") + 2]
    assert "-t" not in argv
    assert "-copyts" in argv
    assert argv.index("-copyts") < ss_positions[0] < argv.index("-i") < argv.index("-to")


def test_subtitles_vtt_invalid_global_track_returns_controlled_404(monkeypatch):
    from stremiosrv.api import subs as subs_api

    # This regression starts after upstream 1.6.20 media resolution.
    # Destination/media guard behaviour has dedicated tests.
    monkeypatch.setattr(
        subs_api,
        "resolve_media_input",
        lambda request, url: url,
    )

    monkeypatch.setattr(
        subs_api,
        "probe_media",
        lambda url: {
            "format": {},
            "streams": [
                {"id": 0, "index": 0, "track": "video", "codec": "h264"},
                {"id": 2, "index": 2, "track": "subtitle", "codec": "subrip", "lang": "eng"},
            ],
            "samples": {},
        },
    )

    c = TestClient(create_app())
    r = c.get(
        "/" + "a" * 40 + "/0/subtitles.vtt",
        params={"mediaURL": "http://media", "track": 31},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "subtitle track not found"


def test_subtitles_vtt_probe_timeout_is_controlled_504(monkeypatch):
    from stremiosrv.api import subs as subs_api

    # This regression starts after upstream 1.6.20 media resolution.
    # Destination/media guard behaviour has dedicated tests.
    monkeypatch.setattr(
        subs_api,
        "resolve_media_input",
        lambda request, url: url,
    )
    from stremiosrv.transcode.probe import ProbeTimeoutError

    def _times_out(url):
        raise ProbeTimeoutError("slow")

    monkeypatch.setattr(subs_api, "probe_media", _times_out)

    c = TestClient(create_app())
    r = c.get(
        "/" + "a" * 40 + "/0/subtitles.vtt",
        params={"mediaURL": "http://media", "track": 31},
    )
    assert r.status_code == 504
    assert r.json()["detail"] == "subtitle probe timed out"


def test_subtitles_list_resolves_the_media_url(monkeypatch):
    """probe_media must receive the resolved (own-or-reader) URL, never the raw client mediaURL --
    the same contract hls.py's probe route gets (Task 7 / Minor 8)."""
    from stremiosrv.api import subs as subs_api

    seen_by_resolve = []
    seen_by_probe = []

    def fake_resolve(request, url):
        seen_by_resolve.append(url)
        return "http://127.0.0.1:1/resolved"

    def fake_probe(url):
        seen_by_probe.append(url)
        return {"format": {"name": "matroska"}, "streams": []}

    monkeypatch.setattr(subs_api, "resolve_media_input", fake_resolve)
    monkeypatch.setattr(subs_api, "probe_media", fake_probe)
    c = TestClient(create_app())
    r = c.get("/" + "a" * 40 + "/0/subtitles.json",
              params={"mediaURL": "https://cdn.example/v.mkv"})
    assert r.status_code == 200
    assert seen_by_resolve == ["https://cdn.example/v.mkv"]
    assert seen_by_probe == ["http://127.0.0.1:1/resolved"]


def test_subtitles_list_refuses_hls_format_input():
    """A torrent whose bytes are themselves an HLS playlist must not be handed to ffprobe's HLS
    demuxer here either -- same Minor-8 concern as hls.py's probe/master, and the same fix: refuse
    a *successful* probe that reports an hls format. ProbeTimeoutError's separate
    `{"subtitles": []}` branch (a slow/failed probe) is untouched -- this is only for a probe that
    succeeded and found a playlist.

    For an own mediaURL (the normal torrent case), resolve_media_input returns it unchanged and
    ffprobe's HLS demuxer can then open absolute LAN segment URLs a malicious torrent's playlist
    names -- the protocol whitelist permits http/https, so it does not stop this on its own."""
    c = TestClient(create_app())
    with patch("stremiosrv.api.subs.resolve_media_input", side_effect=lambda r, u: u), \
         patch("stremiosrv.api.subs.probe_media",
               return_value={"format": {"name": "hls"}, "streams": [], "samples": {}}):
        r = c.get("/" + "a" * 40 + "/0/subtitles.json",
                  params={"mediaURL": "http://127.0.0.1:11470/aabb/0"})
    assert r.status_code == 415


def test_subtitles_vtt_resolves_the_media_url_and_whitelists_protocols(monkeypatch):
    """The raw client URL must never reach ffmpeg and the input protocol whitelist
    must precede -i, while preserving the fork's streaming Popen implementation."""
    import io
    from stremiosrv.api import subs as subs_api

    seen = {}

    class _Proc:
        def __init__(self, argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            self.stdout = io.BytesIO(
                b"WEBVTT\\n\\n00:00:01.000 --> 00:00:02.000\\nhi\\n"
            )
            self._returncode = None

        def wait(self, timeout=None):
            self._returncode = 0
            return 0

        def poll(self):
            return self._returncode

        def terminate(self):
            self._returncode = 0

        def kill(self):
            self._returncode = -9

    monkeypatch.setattr(
        subs_api,
        "resolve_media_input",
        lambda request, url: "http://127.0.0.1:1/resolved",
    )

    monkeypatch.setattr(
        subs_api,
        "probe_media",
        lambda url: {
            "format": {"name": "matroska"},
            "streams": [
                {
                    "id": 31,
                    "index": 31,
                    "track": "subtitle",
                    "codec": "subrip",
                    "lang": "eng",
                }
            ],
            "samples": {},
        },
    )

    monkeypatch.setattr(subs_api.subprocess, "Popen", _Proc)

    c = TestClient(create_app())

    r = c.get(
        "/" + "a" * 40 + "/0/subtitles.vtt",
        params={
            "mediaURL": "https://cdn.example/v.mkv",
            "track": 31,
        },
    )

    assert r.status_code == 200

    argv = seen["argv"]

    assert "http://127.0.0.1:1/resolved" in argv
    assert "https://cdn.example/v.mkv" not in argv

    assert "-protocol_whitelist" in argv
    i = argv.index("-protocol_whitelist")
    assert argv[i + 1] == "file,crypto,data,http,tcp,tls,https"
    assert i < argv.index("-i")

    # Preserve the fork contract: track is FFmpeg's GLOBAL stream index.
    assert ["-map", "0:31"] == argv[
        argv.index("-map"):argv.index("-map") + 2
    ]
    assert "0:s:31" not in argv


def test_subtitles_from_a_refused_destination_is_403():
    c = TestClient(create_app())
    # link-local (cloud-metadata range) is refused to everyone, home or not
    r = c.get("/subtitles.srt", params={"from": "http://169.254.169.254/latest/meta-data/"})
    assert r.status_code == 403


def test_subtitles_from_non_http_is_400():
    c = TestClient(create_app())
    r = c.get("/subtitles.vtt", params={"from": "file:///etc/hostname"})
    assert r.status_code == 400


def test_trace_webvtt_timeline_logs_only_timing_metadata(caplog):
    from stremiosrv.api import subs as subs_api

    payload = (
        b"WEBVTT\n\n"
        b"00:01:02.300 --> 00:01:04.500\nSECRET SUBTITLE TEXT\n\n"
        b"00:01:08.000 --> 00:01:09.250\nANOTHER SECRET\n"
    )
    with caplog.at_level("DEBUG", logger=subs_api.__name__):
        subs_api._trace_webvtt_timeline(payload, 60.0, 30.0)

    msg = caplog.messages[-1]
    assert "window_start=60.000" in msg
    assert "window_duration=30.000" in msg
    assert "cues=2" in msg
    assert "first=00:01:02.300" in msg
    assert "last=00:01:09.250" in msg
    assert "SECRET" not in msg


def test_windowed_webvtt_adds_hls_timestamp_map():
    from stremiosrv.api import subs as subs_api

    payload = b"WEBVTT\n\n00:00:02.000 --> 00:00:04.000\ntext\n"
    out = subs_api._add_webvtt_timestamp_map(payload, 60.0)

    assert out.startswith(
        b"WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0\n"
    )
    assert b"00:00:02.000 --> 00:00:04.000" in out


def test_windowed_webvtt_uses_dynamic_mpegts_start():
    from stremiosrv.api import subs as subs_api

    payload = b"WEBVTT\n\n00:00:02.000 --> 00:00:04.000\ntext\n"
    out = subs_api._add_webvtt_timestamp_map(payload, 60.0, 129750)

    assert b"X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:129750\n" in out


def test_non_windowed_webvtt_is_not_timestamp_mapped():
    from stremiosrv.api import subs as subs_api

    payload = b"WEBVTT\n\n00:00:02.000 --> 00:00:04.000\ntext\n"
    assert subs_api._add_webvtt_timestamp_map(payload, None) == payload


def test_subtitles_vtt_minus_one_route_delegates_to_webvtt_handler(monkeypatch):
    """The literal -1 route must reach the existing WebVTT handler."""

    from stremiosrv.api import subs as subs_api

    captured = {}

    def fake_subtitles_vtt(
        info_hash,
        idx,
        mediaURL,
        request,
        track=0,
        start=None,
        duration=None,
        mpegtsStart=0,
    ):
        captured.update(
            {
                "info_hash": info_hash,
                "idx": idx,
                "mediaURL": mediaURL,
                "track": track,
                "start": start,
                "duration": duration,
                "mpegtsStart": mpegtsStart,
            }
        )
        return subs_api.Response(
            content="WEBVTT\n\n",
            media_type="text/vtt",
        )

    monkeypatch.setattr(subs_api, "subtitles_vtt", fake_subtitles_vtt)

    info_hash = "e" * 40
    media_url = f"https://host/{info_hash}/-1?"

    client = TestClient(create_app())
    response = client.get(
        f"/{info_hash}/-1/subtitles.vtt",
        params={
            "mediaURL": media_url,
            "track": 2,
            "start": 30.0,
            "duration": 30.0,
        },
    )

    assert response.status_code == 200
    assert response.text.startswith("WEBVTT")
    assert response.headers["content-type"].startswith("text/vtt")

    assert captured == {
        "info_hash": info_hash,
        "idx": -1,
        "mediaURL": media_url,
        "track": 2,
        "start": 30.0,
        "duration": 30.0,
        "mpegtsStart": 0,
    }



def test_subtitles_vtt_minus_one_route_is_registered():
    """stremio-core uses /<infohash>/-1 when fileIdx is implicit.

    The literal subtitle route must exist instead of falling through to
    unmatched routing because Starlette's {idx:int} does not match -1.
    """
    app = create_app()

    paths = {
        route.path
        for route in app.routes
        if hasattr(route, "path")
    }

    assert "/{info_hash}/-1/subtitles.vtt" in paths
    assert "/{info_hash}/{idx:int}/subtitles.vtt" in paths


def test_webvtt_window_cache_reuses_completed_payload(monkeypatch):
    import io
    from stremiosrv.api import subs as subs_api

    subs_api._reset_vtt_window_cache()
    calls = 0

    class _Proc:
        def __init__(self):
            self.stdout = io.BytesIO(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n")
            self._returncode = None
        def wait(self, timeout=None):
            self._returncode = 0
            return 0
        def poll(self):
            return self._returncode
        def terminate(self):
            self._returncode = 0
        def kill(self):
            self._returncode = -9

    def factory():
        nonlocal calls
        calls += 1
        return _Proc()

    key = ("a" * 40, 0, 9, 0.0, 30.0)
    first = b"".join(subs_api._cached_webvtt_window_stream(key, factory, 0.0, 30.0))
    second = b"".join(subs_api._cached_webvtt_window_stream(key, factory, 0.0, 30.0))
    assert first == second
    assert first.startswith(b"WEBVTT")
    assert calls == 1


def test_webvtt_window_cache_deduplicates_concurrent_requests():
    import io
    import threading
    import time
    from stremiosrv.api import subs as subs_api

    subs_api._reset_vtt_window_cache()
    calls = 0
    calls_lock = threading.Lock()
    results = []

    class _Proc:
        def __init__(self):
            self.stdout = io.BytesIO(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n")
            self._returncode = None
        def wait(self, timeout=None):
            time.sleep(0.05)
            self._returncode = 0
            return 0
        def poll(self):
            return self._returncode
        def terminate(self):
            self._returncode = 0
        def kill(self):
            self._returncode = -9

    def factory():
        nonlocal calls
        with calls_lock:
            calls += 1
        return _Proc()

    key = ("b" * 40, 0, 9, 30.0, 30.0)
    def worker():
        results.append(b"".join(subs_api._cached_webvtt_window_stream(key, factory, 30.0, 30.0)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert len(results) == 2
    assert results[0] == results[1]
    assert calls == 1
