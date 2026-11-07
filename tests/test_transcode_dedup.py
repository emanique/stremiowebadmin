from pathlib import Path

import pytest

from stremiosrv.transcode import converter as mod


class FakeProc:
    created = []

    def __init__(self, argv, stdout=None, stderr=None):
        self.argv = argv
        self.returncode = None
        self.terminated = False
        self.killed = False
        FakeProc.created.append(self)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


@pytest.fixture(autouse=True)
def fake_popen(monkeypatch):
    FakeProc.created = []
    monkeypatch.setattr(mod.subprocess, "Popen", FakeProc)


@pytest.fixture
def decision():
    return {
        "video": {
            "action": "transcode",
            "codec": "hevc",
        },
        "audio": {
            "action": "copy",
            "codec": "aac",
        },
    }


def test_same_workload_different_job_ids_uses_one_ffmpeg(tmp_path, decision):
    c = mod.Converter(str(tmp_path), "vaapi-h264")

    a = c.ensure_job("client-a", "http://example/video.mkv", decision)
    b = c.ensure_job("client-b", "http://example/video.mkv", decision)

    assert len(FakeProc.created) == 1
    assert a == b
    assert c.active_count() == 1


def test_different_media_uses_different_ffmpeg(tmp_path, decision):
    c = mod.Converter(str(tmp_path), "vaapi-h264")

    a = c.ensure_job("client-a", "http://example/a.mkv", decision)
    b = c.ensure_job("client-b", "http://example/b.mkv", decision)

    assert len(FakeProc.created) == 2
    assert a != b
    assert c.active_count() == 2


def test_different_decision_is_not_deduplicated(tmp_path, decision):
    c = mod.Converter(str(tmp_path), "vaapi-h264")

    other = {
        **decision,
        "video": {
            **decision["video"],
            "scale_width": 1280,
        },
    }

    c.ensure_job("client-a", "http://example/video.mkv", decision)
    c.ensure_job("client-b", "http://example/video.mkv", other)

    assert len(FakeProc.created) == 2
    assert c.active_count() == 2


def test_destroy_one_alias_does_not_stop_shared_encoder(tmp_path, decision):
    c = mod.Converter(str(tmp_path), "vaapi-h264")

    c.ensure_job("client-a", "http://example/video.mkv", decision)
    c.ensure_job("client-b", "http://example/video.mkv", decision)

    proc = FakeProc.created[0]

    c.stop("client-a")

    assert proc.terminated is False
    assert c.active_count() == 1

    c.stop("client-b")

    assert proc.terminated is True
    assert c.active_count() == 0


def test_job_file_for_alias_resolves_shared_output(tmp_path, decision):
    c = mod.Converter(str(tmp_path), "vaapi-h264")

    root_a = c.ensure_job(
        "client-a",
        "http://example/video.mkv",
        decision,
    )
    root_b = c.ensure_job(
        "client-b",
        "http://example/video.mkv",
        decision,
    )

    assert root_a == root_b

    expected = root_a / "seg0.m4s"

    assert c.job_file("client-a", "seg0.m4s") == expected
    assert c.job_file("client-b", "seg0.m4s") == expected


def test_touch_alias_keeps_shared_workload_alive(tmp_path, decision, monkeypatch):
    c = mod.Converter(str(tmp_path), "vaapi-h264")

    now = [100.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])

    c.ensure_job("client-a", "http://example/video.mkv", decision)
    c.ensure_job("client-b", "http://example/video.mkv", decision)

    now[0] = 200.0
    c.touch("client-b")

    now[0] = 250.0

    reaped = c.reap_idle(100.0)

    assert reaped == []
    assert c.active_count() == 1


def test_stop_all_terminates_shared_encoder_once(tmp_path, decision):
    c = mod.Converter(str(tmp_path), "vaapi-h264")

    c.ensure_job("client-a", "http://example/video.mkv", decision)
    c.ensure_job("client-b", "http://example/video.mkv", decision)

    proc = FakeProc.created[0]

    c.stop_all()

    assert proc.terminated is True
    assert c.active_count() == 0
    assert len(FakeProc.created) == 1


def test_concurrent_same_workload_creates_one_encoder(tmp_path, decision):
    import threading

    c = mod.Converter(str(tmp_path), "vaapi-h264")

    barrier = threading.Barrier(3)
    results = []
    errors = []

    def worker(job_id):
        try:
            barrier.wait()
            results.append(
                c.ensure_job(
                    job_id,
                    "http://example/video.mkv",
                    decision,
                )
            )
        except Exception as exc:
            errors.append(exc)

    a = threading.Thread(target=worker, args=("client-a",))
    b = threading.Thread(target=worker, args=("client-b",))

    a.start()
    b.start()

    barrier.wait()

    a.join(timeout=5)
    b.join(timeout=5)

    assert not a.is_alive()
    assert not b.is_alive()
    assert errors == []

    assert len(FakeProc.created) == 1
    assert len(results) == 2
    assert results[0] == results[1]
    assert c.active_count() == 1


def test_rebinding_job_id_to_new_workload_releases_old_owner(
    tmp_path,
    decision,
):
    c = mod.Converter(str(tmp_path), "vaapi-h264")

    c.ensure_job(
        "client-a",
        "http://example/one.mkv",
        decision,
    )

    first = FakeProc.created[0]

    c.ensure_job(
        "client-a",
        "http://example/two.mkv",
        decision,
    )

    assert len(FakeProc.created) == 2

    # Rebinding the final owner must terminate the superseded workload
    # immediately rather than leaving it alive until idle GC.
    assert first.terminated is True
    assert c.active_count() == 1

    # Only the replacement workload remains registered.
    assert len(c._jobs) == 1
    assert len(c._workload_jobs) == 1
    assert c._job_workload["client-a"] in c._jobs

    replacement = FakeProc.created[1]
    assert replacement.terminated is False

    c.stop("client-a")

    assert replacement.terminated is True
    assert c.active_count() == 0


def test_two_clients_destroy_in_reverse_order(tmp_path, decision):
    c = mod.Converter(str(tmp_path), "vaapi-h264")

    c.ensure_job(
        "client-a",
        "http://example/video.mkv",
        decision,
    )
    c.ensure_job(
        "client-b",
        "http://example/video.mkv",
        decision,
    )

    proc = FakeProc.created[0]

    c.stop("client-b")

    assert proc.terminated is False
    assert c.active_count() == 1

    c.stop("client-a")

    assert proc.terminated is True
    assert c.active_count() == 0


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        ({"backend": "vaapi", "device": "/dev/dri/renderD128"}, "h264_vaapi"),
        ({"backend": "nvenc", "device": None}, "h264_nvenc"),
        ({"backend": "none", "device": None}, "libx264"),
    ],
)
def test_auto_transcode_uses_detected_backend(tmp_path, decision, backend, expected):
    argv = mod.build_hls_cmd(
        "http://example/video.mkv", decision, "auto", tmp_path, backend
    )

    assert "-c:v" in argv
    assert argv[argv.index("-c:v") + 1] == expected

    if expected == "h264_vaapi":
        assert "-vaapi_device" in argv
        assert argv[argv.index("-vaapi_device") + 1] == "/dev/dri/renderD128"
        assert "hwupload" in argv[argv.index("-vf") + 1]
    if expected == "h264_nvenc":
        assert "-hwaccel" not in argv


def test_auto_copy_is_never_promoted_by_available_vaapi(tmp_path):
    decision = {
        "video": {"action": "copy", "codec": "hevc"},
        "audio": {"action": "transcode", "codec": "aac"},
    }
    backend = {"backend": "vaapi", "device": "/dev/dri/renderD128"}

    argv = mod.build_hls_cmd(
        "http://example/video.mkv", decision, "auto", tmp_path, backend
    )

    assert argv[argv.index("-c:v") + 1] == "copy"
    assert "h264_vaapi" not in argv
    assert "-vaapi_device" not in argv


def test_auto_converter_detects_backend_once_and_uses_it_for_workload(tmp_path, decision, monkeypatch):
    detected = {"backend": "vaapi", "device": "/dev/dri/renderD129"}
    calls = []

    def fake_detect():
        calls.append(True)
        return detected

    monkeypatch.setattr(mod, "detect_backend", fake_detect)
    c = mod.Converter(str(tmp_path), "auto")
    c.ensure_job("client-auto", "http://example/video.mkv", decision)

    assert len(calls) == 1
    assert c.backend == detected
    assert len(FakeProc.created) == 1
    argv = FakeProc.created[0].argv
    assert argv[argv.index("-c:v") + 1] == "h264_vaapi"
    assert argv[argv.index("-vaapi_device") + 1] == "/dev/dri/renderD129"



def test_auto_vaapi_hdr_transcode_uses_full_gpu_tonemap(tmp_path):
    decision = {
        "video": {"action": "transcode", "scale_width": 3840},
        "audio": {"action": "copy", "codec": "aac"},
        "_streams": [{
            "track": "video",
            "codec": "hevc",
            "width": 3840,
            "isHdr": True,
            "hasMasteringDisplay": True,
            "bitDepth": 10,
            "colorTransfer": "smpte2084",
        }],
    }
    backend = {"backend": "vaapi", "device": "/dev/dri/renderD128"}

    argv = mod.build_hls_cmd(
        "http://example/hdr.mkv", decision, "auto", tmp_path, backend
    )

    assert argv[argv.index("-c:v") + 1] == "h264_vaapi"
    assert argv[argv.index("-hwaccel") + 1] == "vaapi"
    assert argv[argv.index("-hwaccel_output_format") + 1] == "vaapi"
    vf = argv[argv.index("-vf") + 1]
    assert "tonemap_vaapi=" in vf
    assert "transfer=bt709" in vf
    assert "scale_vaapi" not in vf
    assert "hwupload" not in vf


def test_auto_vaapi_hdr_transcode_scales_on_gpu_when_width_is_reduced(tmp_path):
    decision = {
        "video": {"action": "transcode", "scale_width": 1920},
        "audio": {"action": "copy", "codec": "aac"},
        "_streams": [{
            "track": "video",
            "codec": "hevc",
            "width": 3840,
            "isHdr": True,
            "hasMasteringDisplay": True,
            "bitDepth": 10,
            "colorTransfer": "smpte2084",
        }],
    }
    backend = {"backend": "vaapi", "device": "/dev/dri/renderD128"}

    argv = mod.build_hls_cmd(
        "http://example/hdr.mkv", decision, "auto", tmp_path, backend
    )

    vf = argv[argv.index("-vf") + 1]
    assert "tonemap_vaapi=" in vf
    assert "scale_vaapi=w=1920:h=-2:format=nv12" in vf


def test_auto_vaapi_hdr_copy_is_never_promoted_to_transcode(tmp_path):
    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "transcode", "codec": "ac3"},
        "_streams": [{
            "track": "video",
            "codec": "hevc",
            "width": 3840,
            "isHdr": True,
            "bitDepth": 10,
            "colorTransfer": "smpte2084",
        }],
    }
    backend = {"backend": "vaapi", "device": "/dev/dri/renderD128"}

    argv = mod.build_hls_cmd(
        "http://example/hdr.mkv", decision, "auto", tmp_path, backend
    )

    assert argv[argv.index("-c:v") + 1] == "copy"
    assert "-hwaccel" not in argv
    assert "tonemap_vaapi" not in " ".join(argv)



def test_m3u_reader_ticket_rotation_reuses_single_ffmpeg(tmp_path, decision):
    c = mod.Converter(str(tmp_path), "vaapi-h264")
    stable_url = "https://media.example/watch/channel.m3u"
    intent = {"startTimeMs": 0, "videoCodecs": ["h264"], "maxWidth": 3840}
    first = c.ensure_job(
        "playback", "http://127.0.0.1:11470/_hls-media-read/secret/token1",
        decision, source_identity=stable_url, playback_intent=intent,
    )
    changed_probe = {**decision, "_streams": [{"track": "video", "codec": "h264"}]}
    second = c.ensure_job(
        "playback", "http://127.0.0.1:11470/_hls-media-read/secret/token2",
        changed_probe, source_identity=stable_url, playback_intent=intent,
    )
    assert first == second
    assert len(FakeProc.created) == 1
    assert c.active_count() == 1


def test_m3u_explicit_seek_uses_new_workload(tmp_path, decision):
    c = mod.Converter(str(tmp_path), "vaapi-h264")
    src = "https://media.example/watch/channel.m3u"
    for seek in (0, 36000):
        c.ensure_job(
            "playback", "http://127.0.0.1:11470/_hls-media-read/secret/token",
            decision, source_identity=src, playback_intent={"startTimeMs": seek},
        )
    assert len(FakeProc.created) == 2
    assert FakeProc.created[0].terminated


def test_m3u_failed_ffmpeg_does_not_restart_indefinitely(tmp_path, decision):
    c = mod.Converter(str(tmp_path), "vaapi-h264")
    src = "https://media.example/live.m3u8"
    def ensure():
        return c.ensure_job(
            "playback", "http://127.0.0.1:11470/_hls-media-read/secret/ticket",
            decision, source_identity=src, playback_intent={"startTimeMs": 0},
        )
    ensure()
    FakeProc.created[-1].returncode = 1
    ensure()
    FakeProc.created[-1].returncode = 1
    with pytest.raises(mod.TranscodeRetryLimited):
        ensure()
    assert len(FakeProc.created) == 2


def test_m3u_failure_budget_survives_gc(tmp_path, decision):
    c = mod.Converter(str(tmp_path), "vaapi-h264")
    src = "https://media.example/live.m3u8"
    def ensure():
        return c.ensure_job(
            "playback", "http://127.0.0.1:11470/_hls-media-read/secret/token",
            decision, source_identity=src, playback_intent={"startTimeMs": 0},
        )
    ensure()
    FakeProc.created[-1].returncode = 1
    c.reap_idle(0)
    ensure()
    FakeProc.created[-1].returncode = 1
    c.reap_idle(0)
    with pytest.raises(mod.TranscodeRetryLimited):
        ensure()
    assert len(FakeProc.created) == 2



def test_copy_aac_uses_adts_to_asc_filter_for_fmp4(tmp_path):
    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "copy"},
        "_liveInput": True,
        "_streams": [
            {"track": "video", "index": 0, "codec": "h264"},
            {"track": "audio", "index": 1, "codec": "aac"},
        ],
    }
    cmd = mod.build_hls_cmd("https://media.test/live", decision, None, tmp_path)
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "copy"
    assert cmd[cmd.index("-bsf:a") + 1] == "aac_adtstoasc"


def test_copy_non_aac_does_not_use_aac_bitstream_filter(tmp_path):
    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "copy"},
        "_streams": [{"track": "audio", "index": 1, "codec": "opus"}],
    }
    cmd = mod.build_hls_cmd("https://media.test/video", decision, None, tmp_path)
    assert "-bsf:a" not in cmd



def test_live_mpegts_hls_uses_sliding_bounded_playlist(tmp_path):
    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "copy"},
        "_liveInput": True,
        "_streams": [{"track": "audio", "index": 1, "codec": "aac"}],
    }
    cmd = mod.build_hls_cmd("https://media.test/live", decision, None, tmp_path)
    assert cmd[cmd.index("-hls_list_size") + 1] == "8"
    assert "delete_segments+independent_segments" in cmd
    assert "-hls_playlist_type" not in cmd
    assert "-bsf:a" in cmd


def test_vod_hls_stays_event_playlist(tmp_path):
    decision = {"video": {"action": "copy"}, "audio": {"action": "copy"}}
    cmd = mod.build_hls_cmd("https://media.test/movie", decision, None, tmp_path)
    assert cmd[cmd.index("-hls_playlist_type") + 1] == "event"
    assert "-hls_list_size" not in cmd



def test_vod_aac_copy_keeps_original_bitstream_path(tmp_path):
    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "copy"},
        "_streams": [{"track": "audio", "index": 1, "codec": "aac"}],
    }
    argv = mod.build_hls_cmd("https://media.test/recording.mp4", decision, None, tmp_path)
    assert argv[argv.index("-c:a") + 1] == "copy"
    assert "-bsf:a" not in argv
