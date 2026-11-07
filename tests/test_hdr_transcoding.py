"""Regression tests for HDR/VAAPI transcoding policy."""

from stremiosrv.transcode.converter import build_hls_cmd
from stremiosrv.transcode.probe import map_probe


VAAPI = {
    "backend": "vaapi",
    "device": "/dev/dri/renderD128",
}


def _raw_probe(side_data=None, tag="[0][0][0][0]"):
    return {
        "format": {
            "format_name": "matroska,webm",
            "duration": "100.0",
        },
        "streams": [{
            "index": 0,
            "codec_type": "video",
            "codec_name": "hevc",
            "codec_tag_string": tag,
            "width": 3840,
            "height": 2160,
            "r_frame_rate": "24/1",
            "pix_fmt": "yuv420p10le",
            "color_transfer": "smpte2084",
            "color_primaries": "bt2020",
            "color_space": "bt2020nc",
            "side_data_list": side_data or [],
        }],
    }


def _decision(
    *,
    hdr=False,
    dovi=False,
    mastering=False,
    action="transcode",
    width=3840,
    scale_width=1920,
):
    return {
        "video": {
            "action": action,
            "scale_width": scale_width,
        },
        "audio": None,
        "_streams": [{
            "index": 0,
            "track": "video",
            "codec": "hevc",
            "width": width,
            "height": 2160,
            "isHdr": hdr,
            "isDoVi": dovi,
            "hasMasteringDisplay": mastering,
            "colorTransfer": "smpte2084" if hdr else "bt709",
            "colorPrimaries": "bt2020" if hdr else "bt709",
            "colorSpace": "bt2020nc" if hdr else "bt709",
        }],
    }


def _command(decision):
    return build_hls_cmd(
        "https://example.invalid/video.mkv",
        decision,
        "auto",
        "/tmp/hls-test",
        backend=VAAPI,
    )


def _text(argv):
    return " ".join(argv)


def test_probe_hdr_without_mastering_metadata():
    video = map_probe(_raw_probe())["streams"][0]

    assert video["isHdr"] is True
    assert video["isDoVi"] is False
    assert video["hasMasteringDisplay"] is False
    assert video["colorTransfer"] == "smpte2084"
    assert video["colorPrimaries"] == "bt2020"
    assert video["colorSpace"] == "bt2020nc"


def test_probe_hdr_with_mastering_metadata():
    video = map_probe(_raw_probe([
        {"side_data_type": "Mastering display metadata"}
    ]))["streams"][0]

    assert video["isHdr"] is True
    assert video["hasMasteringDisplay"] is True


def test_probe_dolby_vision_side_data():
    video = map_probe(_raw_probe([
        {"side_data_type": "DOVI configuration record"}
    ]))["streams"][0]

    assert video["isHdr"] is True
    assert video["isDoVi"] is True


def test_hdr_without_mastering_uses_full_vaapi_tonemap():
    command = _text(_command(_decision(
        hdr=True,
        mastering=False,
    )))

    assert "-hwaccel vaapi" in command
    assert "-hwaccel_output_format vaapi" in command

    assert "-color_primaries bt2020" in command
    assert "-color_trc smpte2084" in command
    assert "-colorspace bt2020nc" in command

    assert "tonemap_vaapi=" in command
    assert "scale_vaapi=w=1920:h=-2:format=nv12" in command
    assert "-c:v h264_vaapi" in command

    assert "tonemap=tonemap=hable" not in command
    assert "gbrpf32le" not in command
    assert "hwupload" not in command


def test_hdr_with_mastering_uses_full_vaapi_tonemap():
    command = _text(_command(_decision(
        hdr=True,
        mastering=True,
    )))

    assert "tonemap_vaapi=" in command
    assert "-hwaccel vaapi" in command
    assert "-hwaccel_output_format vaapi" in command
    assert "-c:v h264_vaapi" in command

    assert "tonemap=tonemap=hable" not in command


def test_dovi_uses_full_vaapi_with_explicit_colour_metadata():
    command = _text(_command(_decision(
        hdr=True,
        dovi=True,
        mastering=False,
    )))

    assert "-color_primaries bt2020" in command
    assert "-color_trc smpte2084" in command
    assert "-colorspace bt2020nc" in command

    assert "-hwaccel vaapi" in command
    assert "-hwaccel_output_format vaapi" in command

    assert (
        "tonemap_vaapi=format=nv12:"
        "matrix=bt709:primaries=bt709:transfer=bt709"
    ) in command

    assert "scale_vaapi=w=1920:h=-2:format=nv12" in command
    assert "-c:v h264_vaapi" in command

    assert "tonemap=tonemap=hable" not in command
    assert "zscale=" not in command
    assert "gbrpf32le" not in command


def test_copy_is_never_promoted_to_transcoding():
    command = _text(_command(_decision(
        hdr=False,
        action="copy",
    )))

    assert "-c:v copy" in command
    assert "tonemap_vaapi" not in command
    assert "tonemap=tonemap" not in command
    assert "h264_vaapi" not in command


def test_hdr_colour_metadata_is_before_input():
    command = _command(_decision(
        hdr=True,
        dovi=True,
        mastering=False,
    ))

    input_pos = command.index("-i")

    assert command.index("-color_primaries") < input_pos
    assert command.index("-color_trc") < input_pos
    assert command.index("-colorspace") < input_pos


def test_hdr_scale_uses_vaapi_not_software():
    command = _text(_command(_decision(
        hdr=True,
        width=3840,
        scale_width=1920,
    )))

    assert "scale_vaapi=w=1920:h=-2:format=nv12" in command
    assert "scale=1920:-2:flags=lanczos" not in command
    assert "hwupload" not in command
