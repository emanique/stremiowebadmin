"""Regression: WebPlayer must not get an incompatible H.264 4K Main10 rendition."""
from stremiosrv.api.hls import _limit_4k_hevc_browser_transcode


def source(width=3840, bit_depth=10, codec="hevc"):
    return {"codec": codec, "width": width, "bitDepth": bit_depth}


def test_4k_hevc_main10_transcoded_to_1080p_compatible_width():
    decision={"video": {"action": "transcode", "scale_width": 3840}}
    result=_limit_4k_hevc_browser_transcode(decision, source())
    assert result["video"]["scale_width"] == 1920


def test_promotes_4k_hevc_main10_copy_to_explicit_h264_transcode():
    decision={"video": {"action": "copy"}}
    actual=_limit_4k_hevc_browser_transcode(decision, source())
    assert actual["video"] == {"action":"transcode","scale_width":1920}


def test_respects_lower_client_max_width():
    decision={"video": {"action": "transcode", "scale_width": 1280}}
    assert _limit_4k_hevc_browser_transcode(decision, source())["video"]["scale_width"] == 1280


def test_regular_hevc_and_8bit_and_h264_not_affected():
    for video in (source(width=1920),source(bit_depth=8),source(codec="h264")):
        decision={"video": {"action": "transcode", "scale_width": 3840}}
        assert _limit_4k_hevc_browser_transcode(decision,video)["video"]["scale_width"] == 3840

from stremiosrv.transcode.converter import _ffmpeg_failure_summary


def test_ffmpeg_diagnostics_preserve_causal_error():
    raw = """frame=20 fps=18
[hevc @ 0xabc] Failed to decode frame: Invalid data found
Error while decoding stream #0:0: Invalid data found when processing input
Conversion failed!"""
    summary = _ffmpeg_failure_summary(raw)
    assert "Error while decoding" in summary
    assert "Conversion failed!" not in summary


def test_ffmpeg_diagnostics_handle_empty_log():
    assert _ffmpeg_failure_summary("") == "no stderr"


def test_8bit_hevc_copy_remains_direct():
    decision={"video":{"action":"copy"}}
    assert _limit_4k_hevc_browser_transcode(decision,source(bit_depth=8))==decision


def test_lower_resolution_main10_copy_remains_direct():
    decision={"video":{"action":"copy"}}
    assert _limit_4k_hevc_browser_transcode(decision,source(width=1920))==decision

from stremiosrv.api.hls import _apply_hls_direct_codec_policy


def test_web_hls_policy_preserves_h264_copy():
    decision={"video":{"action":"copy"}}
    assert _apply_hls_direct_codec_policy(decision, source(codec="h264"), {"h264"})==decision
    assert decision["video"]["action"]=="copy"


def test_web_hls_policy_converts_hevc_8bit_if_not_direct_allowed():
    decision={"video":{"action":"copy"}}
    result=_apply_hls_direct_codec_policy(decision,source(width=1920,bit_depth=8),{"h264"})
    assert result["video"] == {"action":"transcode","scale_width":1920}


def test_web_hls_policy_respects_operator_hevc_allowlist():
    decision={"video":{"action":"copy"}}
    result=_apply_hls_direct_codec_policy(decision,source(width=1920,bit_depth=8),{"h264","hevc"})
    assert result["video"]["action"] == "copy"


def test_web_hls_4k_main10_can_transcode_even_when_hevc_allowed():
    decision={"video":{"action":"copy"}}
    _apply_hls_direct_codec_policy(decision,source(),{"h264","hevc"})
    result=_limit_4k_hevc_browser_transcode(decision,source())
    assert result["video"] == {"action":"transcode","scale_width":1920}
