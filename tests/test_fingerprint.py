from stremiosrv.transcode.fingerprint import decide

PROBE = {"streams": [
    {"track": "video", "codec": "hevc", "width": 3840, "height": 2160},
    {"track": "audio", "codec": "eac3", "channels": 6},
]}


def test_video_copy_when_supported_and_within_width():
    d = decide(PROBE, ["h264", "hevc"], ["aac"], max_audio_channels=2, max_width=3840)
    assert d["video"]["action"] == "copy"
    assert d["audio"]["action"] == "transcode"  # eac3 not in [aac]


def test_video_transcode_when_codec_unsupported():
    d = decide(PROBE, ["h264"], ["aac", "eac3"], max_audio_channels=6, max_width=3840)
    assert d["video"]["action"] == "transcode"
    assert d["audio"]["action"] == "copy"


def test_video_transcode_when_over_maxwidth():
    d = decide(PROBE, ["hevc"], ["aac"], max_audio_channels=2, max_width=1920)
    assert d["video"]["action"] == "transcode"
    assert d["video"]["scale_width"] == 1920


def test_hdr10_transcodes_when_client_has_no_hdr_capability_signal():
    probe = {"streams": [{
        "track": "video",
        "codec": "hevc",
        "profile": "Main 10",
        "width": 3840,
        "height": 1606,
        "pixFmt": "yuv420p10le",
        "bitDepth": 10,
        "isHdr": True,
        "isDoVi": False,
        "colorTransfer": "smpte2084",
        "colorPrimaries": "bt2020",
    }]}
    d = decide(probe, ["h264", "hevc"], ["aac"], max_audio_channels=2, max_width=3840)
    assert d["video"] == {"action": "transcode", "scale_width": 3840}


def test_hevc_sdr_stays_copy_when_supported():
    probe = {"streams": [{
        "track": "video", "codec": "hevc", "width": 3840, "height": 2160,
        "isHdr": False, "isDoVi": False,
    }]}
    d = decide(probe, ["h264", "hevc"], ["aac"], max_audio_channels=2, max_width=3840)
    assert d["video"]["action"] == "copy"


def test_dolby_vision_transcodes_when_hls_has_no_dovi_display_capability_signal():
    probe = {"streams": [{
        "track": "video", "codec": "hevc", "width": 3840, "height": 2160,
        "isHdr": True, "isDoVi": True,
    }]}
    d = decide(probe, ["h264", "hevc"], ["aac"], max_audio_channels=2, max_width=3840)
    assert d["video"]["action"] == "transcode"
