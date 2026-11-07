from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "docker" / "ffmpeg_wrapper.py"

spec = importlib.util.spec_from_file_location("ffmpeg_wrapper", WRAPPER)
assert spec and spec.loader
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


def test_explicit_vaapi_pipeline_is_preserved(monkeypatch):
    args = [
        "-hide_banner",
        "-init_hw_device", "vaapi=va:/dev/dri/renderD128",
        "-filter_hw_device", "va",
        "-f", "lavfi",
        "-i", "testsrc2=size=1280x720:rate=30",
        "-vf", "format=nv12,hwupload",
        "-c:v", "h264_vaapi",
        "-t", "3",
        "-f", "null", "-",
    ]
    monkeypatch.setattr(wrapper, "_available_encoders", lambda: {"h264_vaapi"})
    monkeypatch.setattr(wrapper.os.path, "exists", lambda path: True)

    transformed, decision = wrapper._apply_profile(
        args,
        "vaapi-full-h264",
        {"transcoding_vaapi_device": "/dev/dri/renderD128"},
    )

    assert transformed == args
    assert "explicit hardware pipeline preserved" in decision


def test_profile_still_rewrites_normal_transcode_command(monkeypatch):
    args = [
        "-i", "input.mkv",
        "-c:v", "libx264",
        "-f", "null", "-",
    ]
    monkeypatch.setattr(wrapper, "_available_encoders", lambda: {"h264_vaapi"})
    monkeypatch.setattr(wrapper.os.path, "exists", lambda path: True)

    transformed, decision = wrapper._apply_profile(
        args,
        "vaapi-h264",
        {"transcoding_vaapi_device": "/dev/dri/renderD128"},
    )

    assert transformed != args
    assert "h264_vaapi" in transformed
    assert "video=libx264->h264_vaapi" in decision


def test_h264_copy_is_preserved_when_h264_is_direct(monkeypatch):
    args = ["-i", "https://example.invalid/video", "-c:v", "copy", "-c:a", "aac", "-f", "hls", "index.m3u8"]
    monkeypatch.setattr(wrapper, "_probe_video_codec", lambda args: "h264")
    transformed, decision = wrapper._apply_profile(args, "vaapi-full-h264", {"transcoding_direct_video_codecs": "h264", "transcoding_vaapi_device": "/dev/dri/renderD128"})
    assert transformed == args
    assert "video=copy preserved" in decision
    assert "source=h264" in decision
    assert "direct=yes" in decision


def test_hevc_copy_is_transcoded_to_h264_vaapi_when_not_direct(monkeypatch):
    args = ["-hide_banner", "-i", "https://example.invalid/video", "-map", "0:v:0", "-c:v", "copy", "-c:a", "aac", "-f", "hls", "index.m3u8"]
    monkeypatch.setattr(wrapper, "_probe_video_codec", lambda args: "hevc")
    monkeypatch.setattr(wrapper, "_available_encoders", lambda: {"h264_vaapi"})
    monkeypatch.setattr(wrapper, "_probe_video_format", lambda args: {"codec_name": "hevc", "profile": "Main", "pix_fmt": "yuv420p"})
    monkeypatch.setattr(wrapper.os.path, "exists", lambda path: True)
    transformed, decision = wrapper._apply_profile(args, "vaapi-full-h264", {"transcoding_direct_video_codecs": "h264", "transcoding_vaapi_device": "/dev/dri/renderD128", "transcoding_video_quality": 22})
    assert transformed != args
    assert transformed[transformed.index("-c:v") + 1] == "h264_vaapi"
    assert transformed[transformed.index("-hwaccel") + 1] == "vaapi"
    assert transformed[transformed.index("-hwaccel_device") + 1] == "/dev/dri/renderD128"
    assert transformed[transformed.index("-hwaccel_output_format") + 1] == "vaapi"
    assert "scale_vaapi" in transformed[transformed.index("-vf") + 1]
    assert "video=copy(hevc)->h264_vaapi" in decision
    assert "decode=vaapi" in decision
    assert "engine=vaapi" in decision


def test_copy_is_preserved_when_source_codec_probe_fails(monkeypatch):
    args = ["-i", "https://example.invalid/video", "-c:v", "copy", "-c:a", "aac", "-f", "hls", "index.m3u8"]
    monkeypatch.setattr(wrapper, "_probe_video_codec", lambda args: None)
    transformed, decision = wrapper._apply_profile(args, "vaapi-full-h264", {"transcoding_direct_video_codecs": "h264", "transcoding_vaapi_device": "/dev/dri/renderD128"})
    assert transformed == args
    assert "video=copy preserved" in decision
    assert "source codec probe unavailable" in decision


def test_hevc_copy_is_preserved_when_hevc_is_direct(monkeypatch):
    args = ["-i", "https://example.invalid/video", "-c:v", "copy", "-f", "hls", "index.m3u8"]
    monkeypatch.setattr(wrapper, "_probe_video_codec", lambda args: "hevc")
    transformed, decision = wrapper._apply_profile(args, "vaapi-full-h264", {"transcoding_direct_video_codecs": "h264,hevc", "transcoding_vaapi_device": "/dev/dri/renderD128"})
    assert transformed == args
    assert "source=hevc" in decision
    assert "direct=yes" in decision


def test_legacy_copy_mode_without_explicit_profile_is_passthrough():
    args = ["-i", "input.mkv", "-c:v", "copy", "-c:a", "copy", "-f", "hls", "index.m3u8"]
    transformed, decision = wrapper._legacy_passthrough(args, {"transcoding_mode": "copy"})
    assert transformed == args
    assert "legacy settings detected (copy)" in decision



def test_full_vaapi_uses_hardware_decode_for_hevc_main10(monkeypatch):
    args = [
        "-hide_banner", "-i", "https://example.invalid/video",
        "-map", "0:v:0", "-c:v", "copy", "-c:a", "aac",
        "-f", "hls", "index.m3u8",
    ]
    monkeypatch.setattr(wrapper, "_probe_video_codec", lambda args: "hevc")
    monkeypatch.setattr(
        wrapper,
        "_probe_video_format",
        lambda args: {"codec_name": "hevc", "profile": "Main 10", "pix_fmt": "yuv420p10le"},
    )
    monkeypatch.setattr(wrapper, "_available_encoders", lambda: {"h264_vaapi"})
    monkeypatch.setattr(wrapper.os.path, "exists", lambda path: True)

    transformed, decision = wrapper._apply_profile(
        args,
        "vaapi-full-h264",
        {
            "transcoding_direct_video_codecs": "h264",
            "transcoding_vaapi_device": "/dev/dri/renderD128",
            "transcoding_video_quality": 22,
        },
    )

    assert transformed[transformed.index("-c:v") + 1] == "h264_vaapi"
    assert transformed[transformed.index("-hwaccel") + 1] == "vaapi"
    assert transformed[transformed.index("-hwaccel_output_format") + 1] == "vaapi"
    assert transformed[transformed.index("-hwaccel_device") + 1] == "/dev/dri/renderD128"
    assert "scale_vaapi" in transformed[transformed.index("-vf") + 1]
    assert "hwupload" not in transformed[transformed.index("-vf") + 1]
    assert "profile=vaapi-full-h264" in decision
    assert "decode=vaapi" in decision
    assert "decode fallback=software" not in decision



def test_auto_preserves_h264_copy_when_direct(monkeypatch):
    args = ["-i", "https://example.invalid/video", "-c:v", "copy", "-c:a", "copy", "-f", "hls", "index.m3u8"]
    monkeypatch.setattr(wrapper, "_probe_video_codec", lambda args: "h264")
    transformed, decision = wrapper._auto_apply(
        args,
        {"transcoding_direct_video_codecs": "h264"},
    )
    assert transformed == args
    assert "core decision authoritative" in decision


def test_auto_converts_hevc_main10_copy_to_full_vaapi_h264(monkeypatch):
    args = [
        "-hide_banner", "-i", "https://example.invalid/video",
        "-map", "0:v:0", "-c:v", "copy", "-c:a", "aac",
        "-f", "hls", "index.m3u8",
    ]
    monkeypatch.setattr(wrapper, "_probe_video_codec", lambda args: "hevc")
    monkeypatch.setattr(
        wrapper,
        "_probe_video_format",
        lambda args: {
            "codec_name": "hevc",
            "profile": "Main 10",
            "pix_fmt": "yuv420p10le",
        },
    )
    monkeypatch.setattr(
        wrapper,
        "_available_encoders",
        lambda: {"h264_vaapi", "libx264"},
    )
    monkeypatch.setattr(wrapper.os.path, "exists", lambda path: True)

    transformed, decision = wrapper._auto_apply(
        args,
        {
            "transcoding_direct_video_codecs": "h264",
            "transcoding_vaapi_device": "/dev/dri/renderD128",
            "transcoding_video_quality": 22,
        },
    )

    assert transformed == args
    assert "core decision authoritative" in decision


def test_auto_uses_cpu_h264_when_vaapi_is_unavailable(monkeypatch):
    args = ["-i", "https://example.invalid/video", "-c:v", "copy", "-f", "hls", "index.m3u8"]
    monkeypatch.setattr(wrapper, "_probe_video_codec", lambda args: "hevc")
    monkeypatch.setattr(wrapper, "_available_encoders", lambda: {"libx264"})
    monkeypatch.setattr(wrapper.os.path, "exists", lambda path: False)

    transformed, decision = wrapper._auto_apply(
        args,
        {"transcoding_direct_video_codecs": "h264"},
    )

    assert transformed == args
    assert "core decision authoritative" in decision


def test_auto_preserves_copy_when_probe_is_unavailable(monkeypatch):
    args = ["-i", "https://example.invalid/video", "-c:v", "copy", "-f", "hls", "index.m3u8"]
    monkeypatch.setattr(wrapper, "_probe_video_codec", lambda args: None)
    transformed, decision = wrapper._auto_apply(
        args,
        {"transcoding_direct_video_codecs": "h264"},
    )
    assert transformed == args
    assert "core decision authoritative" in decision


def test_auto_preserves_explicit_hardware_video_transcode():
    args = ["-i", "input.mkv", "-vf", "format=nv12,hwupload",
            "-c:v", "h264_vaapi", "-f", "hls", "output.m3u8"]
    result, decision = wrapper._auto_apply(args, {})
    assert result == args
    assert "core decision unchanged" in decision
