from stremiosrv.transcode.converter import build_hls_cmd

DEC_TRANSCODE = {"video": {"action": "transcode", "scale_width": 1920},
                 "audio": {"action": "transcode"}}
DEC_COPY = {"video": {"action": "copy"}, "audio": {"action": "copy"}}


def test_nvenc_hls():
    cmd = build_hls_cmd("http://x/0", DEC_TRANSCODE, "nvenc-linux", "/tmp/j")
    assert "h264_nvenc" in cmd
    assert "-hwaccel" in cmd and "cuda" in cmd
    assert any("scale=1920" in p for p in cmd)
    assert "aac" in cmd
    assert "hls" in cmd and "/tmp/j/index.m3u8" in cmd


def test_vaapi_encode_only_hls():
    """Encode-only VAAPI uses software decode and uploads NV12 frames."""
    cmd = build_hls_cmd(
        "http://x/0",
        DEC_TRANSCODE,
        "vaapi-h264",
        "/tmp/j",
    )

    assert "h264_vaapi" in cmd
    assert "-hwaccel" not in cmd
    assert "-hwaccel_output_format" not in cmd

    vf = cmd[cmd.index("-vf") + 1]

    assert "scale=1920:-2:flags=lanczos" in vf
    assert "format=nv12" in vf
    assert "hwupload" in vf


def test_vaapi_encode_only_without_scale_uploads_nv12():
    """Software-decoded frames must be uploaded before VAAPI encode."""
    cmd = build_hls_cmd(
        "http://x/0",
        {"video": {"action": "transcode"}},
        "vaapi-h264",
        "/tmp/j",
    )

    assert "-hwaccel" not in cmd
    assert "-hwaccel_output_format" not in cmd

    vf = cmd[cmd.index("-vf") + 1]

    assert vf == "format=nv12,hwupload"
    assert cmd[cmd.index("-c:v") + 1] == "h264_vaapi"


def test_vaapi_full_h264_uses_hardware_decode():
    """Full VAAPI keeps decode, filtering and encode on VAAPI."""
    cmd = build_hls_cmd(
        "http://x/0",
        DEC_TRANSCODE,
        "vaapi-full-h264",
        "/tmp/j",
    )

    assert "-hwaccel" in cmd
    assert cmd[cmd.index("-hwaccel") + 1] == "vaapi"

    assert "-hwaccel_output_format" in cmd
    assert (
        cmd[cmd.index("-hwaccel_output_format") + 1]
        == "vaapi"
    )

    vf = cmd[cmd.index("-vf") + 1]

    assert vf == "scale_vaapi=w=1920:h=-2:format=nv12"
    assert cmd[cmd.index("-c:v") + 1] == "h264_vaapi"


def test_cpu_hls():
    cmd = build_hls_cmd("http://x/0", DEC_TRANSCODE, None, "/tmp/j")
    assert "libx264" in cmd


def test_copy_hls_has_no_decode_accel():
    cmd = build_hls_cmd("http://x/0", DEC_COPY, "nvenc-linux", "/tmp/j")
    assert "copy" in cmd
    assert "-hwaccel" not in cmd


def test_no_audio_stream():
    cmd = build_hls_cmd("http://x/0", {"video": {"action": "copy"}}, None, "/tmp/j")
    assert "0:a:0?" not in cmd


def test_multitrack_hls_exposes_audio_group_and_keeps_subtitles_native():
    decision = {
        "video": {"action": "transcode", "scale_width": 1920},
        "audio": {"action": "transcode"},
        "_streams": [
            {"track": "video", "index": 0, "codec": "hevc"},
            {"track": "audio", "index": 1, "codec": "eac3", "lang": "eng"},
            {"track": "audio", "index": 2, "codec": "aac", "lang": "por"},
            {"track": "subtitle", "index": 3, "codec": "subrip", "lang": "eng"},
        ],
    }
    cmd = build_hls_cmd("http://x/0", decision, "vaapi-renderD128", "/tmp/j")
    stream_map = cmd[cmd.index("-var_stream_map") + 1]
    assert "a:0,agroup:audio,default:yes,language:eng" in stream_map
    assert "a:1,agroup:audio,default:no,language:por" in stream_map
    assert "v:0,agroup:audio" in stream_map
    assert "sgroup:" not in stream_map
    assert "sname:" not in stream_map
    assert "-c:s" not in cmd
    assert "0:3?" not in cmd
    assert "-hls_subtitle_path" not in cmd
    assert "mpegts" in cmd
    assert "/tmp/j/stream_%v.m3u8" in cmd
    assert cmd.count("-map") == 3


def test_bitmap_subtitles_are_not_mapped_into_hls_audio_renditions():
    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "copy"},
        "_streams": [
            {"track": "video", "index": 0, "codec": "h264"},
            {"track": "audio", "index": 1, "codec": "aac", "lang": "eng"},
            {"track": "audio", "index": 2, "codec": "aac", "lang": "por"},
            {"track": "subtitle", "index": 3, "codec": "hdmv_pgs_subtitle", "lang": "eng"},
        ],
    }
    cmd = build_hls_cmd("http://x/0", decision, None, "/tmp/j")
    stream_map = cmd[cmd.index("-var_stream_map") + 1]
    assert "sgroup:subs" not in stream_map
    assert "-c:s" not in cmd
    assert "0:3?" not in cmd


def test_single_track_path_keeps_fmp4_layout():
    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "copy"},
        "_streams": [
            {"track": "video", "index": 0, "codec": "h264"},
            {"track": "audio", "index": 1, "codec": "aac", "lang": "eng"},
        ],
    }
    cmd = build_hls_cmd("http://x/0", decision, None, "/tmp/j")
    assert "-var_stream_map" not in cmd
    assert "fmp4" in cmd
    assert "mpegts" not in cmd
    assert "init.mp4" in cmd
    assert "/tmp/j/seg%d.m4s" in cmd
    assert "/tmp/j/index.m3u8" in cmd


def test_build_hls_cmd_has_a_protocol_whitelist_before_input():
    """ffmpeg must not be free to follow whatever scheme a redirect throws at it -- only the
    handful this server actually serves media over (Minor 8's protocol whitelist), and it has to
    precede -i to guard the input it names."""
    argv = build_hls_cmd("http://127.0.0.1:1/x", DEC_COPY, None, "/tmp/j")
    assert "-protocol_whitelist" in argv
    i = argv.index("-protocol_whitelist")
    assert argv[i + 1] == "file,crypto,data,http,tcp,tls,https"
    assert i < argv.index("-i")


def test_single_track_hls_transcode_uses_192k_audio():
    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "transcode"},
    }

    cmd = build_hls_cmd(
        "http://x/0",
        decision,
        None,
        "/tmp/j",
    )

    assert "-ab" in cmd
    assert cmd[cmd.index("-ab") + 1] == "192000"
    assert "384000" not in cmd


def test_multitrack_hls_uses_192k_audio():
    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "transcode"},
        "_streams": [
            {
                "track": "video",
                "index": 0,
                "codec": "h264",
            },
            {
                "track": "audio",
                "index": 1,
                "codec": "eac3",
                "lang": "eng",
            },
            {
                "track": "audio",
                "index": 2,
                "codec": "aac",
                "lang": "por",
            },
        ],
    }

    cmd = build_hls_cmd(
        "http://x/0",
        decision,
        None,
        "/tmp/j",
    )

    assert "-b:a" in cmd
    assert cmd[cmd.index("-b:a") + 1] == "192k"
    assert "384k" not in cmd


def test_full_vaapi_preserves_hevc_copy_decision(monkeypatch):
    """GPU availability must not override Stremio Direct Stream."""
    monkeypatch.setenv(
        "TRANSCODING_DIRECT_VIDEO_CODECS",
        "h264",
    )

    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "copy"},
        "_streams": [
            {
                "track": "video",
                "index": 0,
                "codec": "hevc",
            },
            {
                "track": "audio",
                "index": 1,
                "codec": "aac",
            },
        ],
    }

    cmd = build_hls_cmd(
        "http://x/0",
        decision,
        "vaapi-full-h264",
        "/tmp/j",
    )

    i = cmd.index("-c:v")

    assert cmd[i + 1] == "copy"
    assert "-hwaccel" not in cmd



def test_full_vaapi_preserves_h264_copy_when_h264_direct(monkeypatch):
    monkeypatch.setenv("TRANSCODING_DIRECT_VIDEO_CODECS", "h264")

    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "copy"},
        "_streams": [
            {"track": "video", "index": 0, "codec": "h264"},
            {"track": "audio", "index": 1, "codec": "aac"},
        ],
    }

    cmd = build_hls_cmd(
        "http://x/0",
        decision,
        "vaapi-full-h264",
        "/tmp/j",
    )

    i = cmd.index("-c:v")
    assert cmd[i + 1] == "copy"
    assert "h264_vaapi" not in cmd


def test_full_vaapi_preserves_hevc_when_explicitly_direct(monkeypatch):
    monkeypatch.setenv(
        "TRANSCODING_DIRECT_VIDEO_CODECS",
        "h264,hevc",
    )

    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "copy"},
        "_streams": [
            {"track": "video", "index": 0, "codec": "hevc"},
        ],
    }

    cmd = build_hls_cmd(
        "http://x/0",
        decision,
        "vaapi-full-h264",
        "/tmp/j",
    )

    i = cmd.index("-c:v")
    assert cmd[i + 1] == "copy"


def test_encode_only_vaapi_preserves_hevc_copy(monkeypatch):
    """AUTO backend must not convert copy into transcoding."""
    monkeypatch.setenv(
        "TRANSCODING_DIRECT_VIDEO_CODECS",
        "h264",
    )

    decision = {
        "video": {"action": "copy"},
        "_streams": [
            {
                "track": "video",
                "index": 0,
                "codec": "hevc",
            },
        ],
    }

    cmd = build_hls_cmd(
        "http://x/0",
        decision,
        "vaapi-h264",
        "/tmp/j",
    )

    i = cmd.index("-c:v")

    assert cmd[i + 1] == "copy"



def test_encode_only_vaapi_preserves_direct_h264(monkeypatch):
    monkeypatch.setenv("TRANSCODING_DIRECT_VIDEO_CODECS", "h264")

    decision = {
        "video": {"action": "copy"},
        "_streams": [
            {"track": "video", "index": 0, "codec": "h264"},
        ],
    }

    cmd = build_hls_cmd(
        "http://x/0",
        decision,
        "vaapi-h264",
        "/tmp/j",
    )

    i = cmd.index("-c:v")
    assert cmd[i + 1] == "copy"
    assert "-hwaccel" not in cmd


def test_single_track_hls_does_not_delegate_master_to_ffmpeg():
    """Single-track master is application-owned; FFmpeg writes index/fMP4 only."""
    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "copy"},
        "_streams": [
            {"track": "video", "index": 0, "codec": "h264"},
            {"track": "audio", "index": 1, "codec": "aac", "lang": "eng"},
        ],
    }

    cmd = build_hls_cmd("http://x/0", decision, None, "/tmp/j")

    assert "-master_pl_name" not in cmd
    assert "-var_stream_map" not in cmd
    assert "fmp4" in cmd
    assert "mpegts" not in cmd
    assert "/tmp/j/index.m3u8" in cmd
    assert "/tmp/j/seg%d.m4s" in cmd
    assert "init.mp4" in cmd


def test_multitrack_hls_still_delegates_master_to_ffmpeg():
    """Multi-track master remains FFmpeg-owned because it uses var_stream_map."""
    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "copy"},
        "_streams": [
            {"track": "video", "index": 0, "codec": "h264"},
            {"track": "audio", "index": 1, "codec": "aac", "lang": "eng"},
            {"track": "audio", "index": 2, "codec": "aac", "lang": "por"},
        ],
    }

    cmd = build_hls_cmd("http://x/0", decision, None, "/tmp/j")

    assert "-var_stream_map" in cmd
    assert "-master_pl_name" in cmd
    assert cmd[cmd.index("-master_pl_name") + 1] == "master.m3u8"


def test_single_track_master_contains_real_newlines(tmp_path):
    """Single-track HLS master must be a real multiline M3U8 playlist."""
    from stremiosrv.transcode.converter import _write_single_track_master

    master = _write_single_track_master(tmp_path)

    raw = master.read_bytes()
    text = raw.decode("utf-8")

    assert b"\\n" not in raw

    assert text == (
        "#EXTM3U\n"
        "#EXT-X-VERSION:7\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000\n"
        "index.m3u8\n"
    )

    assert text.splitlines() == [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000",
        "index.m3u8",
    ]


def test_incomplete_torrent_hls_input_is_paced_at_realtime():
    decision = dict(DEC_TRANSCODE)
    decision["_realtimeInput"] = True
    cmd = build_hls_cmd("http://x/0", decision, None, "/tmp/j")
    i = cmd.index("-i")
    assert cmd[i - 1] == "-re"
    assert cmd[i + 1] == "http://x/0"


def test_complete_hls_input_is_not_realtime_paced():
    cmd = build_hls_cmd("http://x/0", DEC_TRANSCODE, None, "/tmp/j")
    assert "-re" not in cmd


def test_resume_start_time_is_input_seek_before_media():
    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "copy"},
        "_startTimeMs": 600686,
    }
    cmd = build_hls_cmd("http://x/0", decision, None, "/tmp/j")
    assert cmd[cmd.index("-ss") + 1] == "600.686"
    assert cmd.index("-ss") < cmd.index("-i")


def test_zero_resume_start_time_does_not_add_seek():
    decision = {
        "video": {"action": "copy"},
        "audio": {"action": "copy"},
        "_startTimeMs": 0,
    }
    cmd = build_hls_cmd("http://x/0", decision, None, "/tmp/j")
    assert "-ss" not in cmd
