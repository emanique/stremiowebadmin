from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webadmin"))

import transcoding_profiles as profiles  # noqa: E402


def _item(profile_id: str, available: bool) -> dict[str, object]:
    return {"id": profile_id, "available": available}


def test_recommends_full_vaapi_h264_before_encode_only_and_cpu():
    items = [
        _item("preserve", True),
        _item("vaapi-h264", True),
        _item("vaapi-full-h264", True),
        _item("cpu-h264", True),
    ]
    assert profiles._recommended_profile(items) == "vaapi"


def test_recommends_encode_only_vaapi_when_full_pipeline_is_unavailable():
    items = [
        _item("preserve", True),
        _item("vaapi-h264", True),
        _item("vaapi-full-h264", False),
        _item("cpu-h264", True),
    ]
    assert profiles._recommended_profile(items) == "vaapi"


def test_nvenc_has_priority_when_runtime_verified():
    items = [
        _item("nvenc-h264", True),
        _item("vaapi-full-h264", True),
        _item("cpu-h264", True),
    ]
    assert profiles._recommended_profile(items) == "nvenc"


def test_vaapi_is_reported_as_gpu_acceleration_for_lxc_render_node():
    items = [
        _item("vaapi-h264", True),
        _item("vaapi-full-h264", False),
        _item("cpu-h264", True),
    ]
    detected = profiles._hardware_detection(items, "/dev/dri/renderD128")

    assert detected["detected"] is True
    assert detected["accelerator"] == "vaapi"
    assert detected["device"] == "/dev/dri/renderD128"
    assert detected["lxcCompatible"] is True


def test_cpu_is_reported_when_no_hardware_profile_passes():
    items = [
        _item("vaapi-h264", False),
        _item("nvenc-h264", False),
        _item("cpu-h264", True),
    ]
    detected = profiles._hardware_detection(items, "/dev/dri/renderD128")

    assert detected["detected"] is False
    assert detected["accelerator"] == "cpu"
    assert detected["device"] is None


def test_process_metadata_extracts_pid_cpu_memory_and_elapsed():
    command = "741 94.0 0.2 00:10 /usr/local/libexec/stremio/ffmpeg-real -i input.mkv -c:v copy out.m3u8"
    argv, process = profiles.base.base._process_metadata(command)
    assert argv.startswith("/usr/local/libexec/stremio/ffmpeg-real")
    assert process == {"pid": 741, "cpuPercent": 94.0, "memoryPercent": 0.2, "elapsed": "00:10"}


def test_source_codec_is_read_from_ffmpeg_log_when_policy_does_not_expose_it():
    log = """
Input #0, matroska,webm, from 'input.mkv':
  Stream #0:0: Video: hevc, yuv420p, 1920x800
  Stream #0:1: Audio: ac3, 48000 Hz, stereo
"""
    telemetry = profiles.base.base
    assert telemetry._source_codec_from_log(log, "video") == "hevc"
    assert telemetry._source_codec_from_log(log, "audio") == "ac3"


def test_runtime_fix_preserves_pid_telemetry_from_container_ps():
    import transcoding_runtime_fix as runtime

    command = "741 94.0 0.2 00:10 /usr/local/libexec/stremio/ffmpeg-real -i input.mkv -c:v copy out.m3u8"
    argv, process = runtime._process_metadata(command)

    assert argv.startswith("/usr/local/libexec/stremio/ffmpeg-real")
    assert process == {
        "pid": 741,
        "cpuPercent": 94.0,
        "memoryPercent": 0.2,
        "elapsed": "00:10",
    }


def test_runtime_extracts_torrent_source_from_ffmpeg_input():
    import transcoding_runtime_fix as runtime

    info_hash = "E88A21AD7ACD934976D5C69C168B0568701B3884"
    argv = (
        "/usr/local/libexec/stremio/ffmpeg-real -hide_banner "
        f"-i https://host:12470/{info_hash}/7? "
        "-c:v h264_vaapi -c:a aac /root/.stremio-server/transcode/job1/index.m3u8"
    )

    source_hash, source_idx = runtime._source_stream_from_argv(argv)

    assert source_hash == info_hash.lower()
    assert source_idx == 7


def test_runtime_source_parser_ignores_non_torrent_inputs():
    import transcoding_runtime_fix as runtime

    assert runtime._source_stream_from_argv(
        "/usr/local/libexec/stremio/ffmpeg-real -i input.mkv -c:v copy out.m3u8"
    ) == (None, None)


def test_runtime_session_exposes_source_identity(monkeypatch):
    import transcoding_runtime_fix as runtime

    info_hash = "a" * 40
    command = (
        "741 94.0 0.2 00:10 /usr/local/libexec/stremio/ffmpeg-real "
        f"-i https://host:12470/{info_hash}/2? "
        "-c:v h264_vaapi -c:a aac "
        "/root/.stremio-server/transcode/job123/index.m3u8"
    )
    monkeypatch.setattr(runtime.base, "_read_job_log", lambda *args: "")

    session = runtime._session_from_command(object(), "/cache", command)

    assert session["jobId"] == "job123"
    assert session["sourceInfoHash"] == info_hash
    assert session["sourceFileIdx"] == 2


def test_latest_job_log_preserves_policy_outside_last_80_lines():
    from webadmin import transcoding_config as tc

    policy = (
        "[ffmpeg-policy] profile=vaapi-full-h264; "
        "video=copy(hevc)->h264_vaapi; "
        "decode=vaapi; engine=vaapi"
    )

    tail_lines = [f"frame={n} fps=100 speed=4.0x" for n in range(1, 81)]
    returned_log = policy + "\n" + "\n".join(tail_lines) + "\n"

    class Result:
        exit_code = 0
        output = returned_log.encode()

    class Container:
        def __init__(self):
            self.command = None

        def exec_run(self, command):
            self.command = command
            return Result()

    container = Container()

    text = tc._latest_job_log(container, "/root/.stremio-server")

    assert container.command[0:2] == ["sh", "-c"]
    assert 'grep "\\[ffmpeg-policy\\]"' in container.command[2]
    assert 'tail -n 80 "$latest"' in container.command[2]

    decision = tc._parse_policy_log(text)

    assert decision is not None
    assert decision["profile"] == "vaapi-full-h264"
    assert decision["upstreamVideoTarget"] == "copy(hevc)"
    assert decision["targetVideo"] == "h264_vaapi"
    assert "decode=vaapi" in decision["decision"]
    assert "engine=vaapi" in decision["decision"]


def test_read_job_log_preserves_policy_and_progress():
    from webadmin import transcoding_config as tc

    policy = "[ffmpeg-policy] profile=vaapi-full-h264; video=copy(hevc)->h264_vaapi; decode=vaapi; engine=vaapi"
    returned_log = policy + "\n" + "\n".join(f"frame={n} fps=100 speed=4.0x" for n in range(1, 81)) + "\n"

    class Result:
        exit_code = 0
        output = returned_log.encode()

    class Container:
        def __init__(self):
            self.command = None

        def exec_run(self, command):
            self.command = command
            return Result()

    container = Container()
    text = tc._read_job_log(container, "/root/.stremio-server", "job123")

    assert container.command[0:2] == ["sh", "-c"]
    assert "ffmpeg-policy" in container.command[2]
    assert "tail -n 80" in container.command[2]

    decision = tc._parse_policy_log(text)
    assert decision is not None
    assert decision["profile"] == "vaapi-full-h264"
    assert decision["targetVideo"] == "h264_vaapi"


def test_backend_matrix_keeps_detected_nvidia_separate_from_nvenc_capability(monkeypatch):
    items = [
        {"id": "vaapi-h264", "available": True, "reason": "ok"},
        {"id": "vaapi-hevc", "available": False, "reason": "no"},
        {"id": "nvenc-h264", "available": False, "reason": "unsupported device"},
        {"id": "nvenc-hevc", "available": False, "reason": "unsupported device"},
        {"id": "cpu-h264", "available": True, "reason": "ok"},
        {"id": "cpu-hevc", "available": True, "reason": "ok"},
    ]

    monkeypatch.setattr(
        profiles.base,
        "_exists",
        lambda container, path, executable=False: path in {
            "/dev/dri/renderD129",
            "/dev/nvidia0",
        },
    )

    monkeypatch.setattr(
        profiles,
        "_nvidia_runtime_info",
        lambda container: {
            "detected": True,
            "runtime": True,
            "name": "NVIDIA GeForce 920M",
            "driver": "470.256.02",
        },
    )

    matrix = profiles._backend_matrix(
        object(),
        items,
        "/dev/dri/renderD129",
    )

    assert matrix["vaapi"]["detected"] is True
    assert matrix["vaapi"]["availableForAuto"] is True

    assert matrix["nvidia"]["detected"] is True
    assert matrix["nvidia"]["runtime"] is True
    assert matrix["nvidia"]["h264"] is False
    assert matrix["nvidia"]["availableForAuto"] is False

    assert matrix["cpu"]["availableForAuto"] is True


def test_backend_matrix_marks_verified_nvenc_selectable(monkeypatch):
    items = [
        {"id": "vaapi-h264", "available": False, "reason": "no"},
        {"id": "vaapi-hevc", "available": False, "reason": "no"},
        {"id": "nvenc-h264", "available": True, "reason": "ok"},
        {"id": "nvenc-hevc", "available": False, "reason": "no"},
        {"id": "cpu-h264", "available": True, "reason": "ok"},
        {"id": "cpu-hevc", "available": True, "reason": "ok"},
    ]

    monkeypatch.setattr(
        profiles.base,
        "_exists",
        lambda container, path, executable=False: path == "/dev/nvidia0",
    )

    monkeypatch.setattr(
        profiles,
        "_nvidia_runtime_info",
        lambda container: {
            "detected": True,
            "runtime": True,
            "name": "Compatible NVIDIA GPU",
            "driver": "999.0",
        },
    )

    matrix = profiles._backend_matrix(
        object(),
        items,
        "/dev/dri/renderD129",
    )

    assert matrix["nvidia"]["detected"] is True
    assert matrix["nvidia"]["runtime"] is True
    assert matrix["nvidia"]["h264"] is True
    assert matrix["nvidia"]["availableForAuto"] is True


def test_profiles_uses_container_vaapi_device_when_not_persisted(monkeypatch):
    class DummyContainer:
        pass

    container = DummyContainer()

    class Containers:
        def get(self, name):
            return container

    class Client:
        containers = Containers()

    monkeypatch.setattr(profiles.legacy, "client", lambda: Client())
    monkeypatch.setattr(profiles.legacy, "read_config", lambda: {})
    monkeypatch.setattr(
        profiles.base,
        "_ffmpeg_binary",
        lambda c: "/usr/bin/ffmpeg",
    )
    monkeypatch.setattr(
        profiles.base.base,
        "_container_env",
        lambda c: {"VAAPI_DEVICE": "/dev/dri/renderD129"},
    )

    seen = []

    def fake_test_profile(c, profile_id, device, binary):
        seen.append(device)
        return profiles._result(
            profile_id,
            profile_id == "preserve",
            "test",
        )

    monkeypatch.setattr(
        profiles,
        "_test_profile",
        fake_test_profile,
    )
    monkeypatch.setattr(
        profiles,
        "_backend_matrix",
        lambda c, items, device: {"device": device},
    )

    profiles.PROFILE_CACHE["at"] = 0.0
    profiles.PROFILE_CACHE["value"] = None

    data = profiles._profiles(force=True)

    assert data["device"] == "/dev/dri/renderD129"
    assert seen
    assert all(
        device == "/dev/dri/renderD129"
        for device in seen
    )


def test_profiles_prefers_valid_container_device_over_stale_persisted_device(monkeypatch):
    class DummyContainer:
        pass

    container = DummyContainer()

    class Containers:
        def get(self, name):
            return container

    class Client:
        containers = Containers()

    monkeypatch.setattr(profiles.legacy, "client", lambda: Client())
    monkeypatch.setattr(
        profiles.legacy,
        "read_config",
        lambda: {"transcoding_vaapi_device": "/dev/dri/renderD128"},
    )
    monkeypatch.setattr(
        profiles.base,
        "_ffmpeg_binary",
        lambda c: "/usr/bin/ffmpeg",
    )
    monkeypatch.setattr(
        profiles.base.base,
        "_container_env",
        lambda c: {"VAAPI_DEVICE": "/dev/dri/renderD129"},
    )
    monkeypatch.setattr(
        profiles.base,
        "_exists",
        lambda c, path, executable=False: path == "/dev/dri/renderD129",
    )

    seen = []

    def fake_test_profile(c, profile_id, device, binary):
        seen.append(device)
        return profiles._result(
            profile_id,
            profile_id == "preserve",
            "test",
        )

    monkeypatch.setattr(profiles, "_test_profile", fake_test_profile)
    monkeypatch.setattr(
        profiles,
        "_backend_matrix",
        lambda c, items, device: {"device": device},
    )

    profiles.PROFILE_CACHE["at"] = 0.0
    profiles.PROFILE_CACHE["value"] = None

    data = profiles._profiles(force=True)

    assert data["device"] == "/dev/dri/renderD129"
    assert seen
    assert all(device == "/dev/dri/renderD129" for device in seen)


def test_nvenc_diagnostics_reports_api_incompatibility():
    class Result:
        output = (
            b"[h264_nvenc] Loaded Nvenc version 11.1\n"
            b"[h264_nvenc] Driver does not support the required nvenc API version. "
            b"Required: 12.0 Found: 11.1\n"
            b"Conversion failed!\n"
        )

    diagnostics = profiles._nvenc_diagnostics(Result())

    assert diagnostics["failureCode"] == "nvenc-api-incompatible"
    assert diagnostics["nvencApiCompatible"] is False
    assert diagnostics["nvencApiRequired"] == "12.0"
    assert diagnostics["nvencApiAvailable"] == "11.1"
    assert "required 12.0" in diagnostics["reason"]
    assert "available 11.1" in diagnostics["reason"]


def test_backend_matrix_exposes_nvenc_api_details(monkeypatch):
    items = [
        {"id": "vaapi-h264", "available": True, "reason": "ok"},
        {"id": "vaapi-hevc", "available": True, "reason": "ok"},
        {
            "id": "nvenc-h264",
            "available": False,
            "reason": "NVIDIA detected and runtime ready, but NVENC API is incompatible.",
            "nvencApiCompatible": False,
            "nvencApiRequired": "12.0",
            "nvencApiAvailable": "11.1",
        },
        {
            "id": "nvenc-hevc",
            "available": False,
            "reason": "NVIDIA detected and runtime ready, but NVENC API is incompatible.",
            "nvencApiCompatible": False,
            "nvencApiRequired": "12.0",
            "nvencApiAvailable": "11.1",
        },
        {"id": "cpu-h264", "available": True, "reason": "ok"},
        {"id": "cpu-hevc", "available": True, "reason": "ok"},
    ]

    monkeypatch.setattr(
        profiles.base,
        "_exists",
        lambda container, path, executable=False: path in {
            "/dev/dri/renderD129",
            "/dev/nvidia0",
        },
    )
    monkeypatch.setattr(
        profiles,
        "_nvidia_runtime_info",
        lambda container: {
            "detected": True,
            "runtime": True,
            "name": "NVIDIA GeForce 920M",
            "driver": "470.256.02",
        },
    )

    matrix = profiles._backend_matrix(object(), items, "/dev/dri/renderD129")
    nvidia = matrix["nvidia"]

    assert nvidia["detected"] is True
    assert nvidia["runtime"] is True
    assert nvidia["availableForAuto"] is False
    assert nvidia["nvencApiCompatible"] is False
    assert nvidia["nvencApiRequired"] == "12.0"
    assert nvidia["nvencApiAvailable"] == "11.1"


def _auto_profile_item(
    profile_id,
    available=True,
    verified=True,
    reason="ok",
):
    return {
        "id": profile_id,
        "available": available,
        "verified": verified,
        "reason": reason,
    }


def _auto_capabilities(
    recommended,
    *,
    vaapi_available=True,
    vaapi_verified=True,
    nvenc_available=False,
):
    return {
        "profiles": [
            _auto_profile_item("preserve"),
            _auto_profile_item("cpu-h264"),
            _auto_profile_item(
                "vaapi-full-h264",
                vaapi_available,
                vaapi_verified,
                "VAAPI unavailable"
                if not vaapi_available
                else "ok",
            ),
            _auto_profile_item(
                "nvenc-h264",
                nvenc_available,
                nvenc_available,
                "NVENC unavailable"
                if not nvenc_available
                else "ok",
            ),
        ],
        "recommendedProfile": recommended,
    }


def test_auto_persists_auto_mode(monkeypatch):
    writes = []

    monkeypatch.setattr(
        profiles.legacy,
        "read_config",
        lambda: {},
    )

    monkeypatch.setattr(
        profiles.legacy,
        "write_config",
        lambda data: writes.append(dict(data)),
    )

    monkeypatch.setattr(
        profiles.legacy,
        "audit",
        lambda *args, **kwargs: None,
    )

    result = profiles.set_profile(
        profiles.ProfileBody(
            profile="auto",
            quality=22,
        )
    )

    assert result["ok"] is True
    assert result["profile"] == "auto"
    assert result["mode"] == "auto"
    assert result["resolvedProfile"] is None

    assert writes

    saved = writes[-1]

    assert saved["transcoding_profile"] == "auto"
    assert saved["transcoding_resolved_profile"] == ""
    assert saved["transcoding_mode"] == "auto"
    assert saved["transcoding_hwaccel"] == "auto"
    assert saved["transcoding_video_codec"] == "auto"
    assert saved["transcoding_video_quality"] == 22


def test_auto_preserves_unrelated_settings(monkeypatch):
    writes = []

    monkeypatch.setattr(
        profiles.legacy,
        "read_config",
        lambda: {
            "max_streams": 5,
            "debug_logs": True,
        },
    )

    monkeypatch.setattr(
        profiles.legacy,
        "write_config",
        lambda data: writes.append(dict(data)),
    )

    monkeypatch.setattr(
        profiles.legacy,
        "audit",
        lambda *args, **kwargs: None,
    )

    profiles.set_profile(
        profiles.ProfileBody(
            profile="auto",
            quality=24,
        )
    )

    saved = writes[-1]

    assert saved["max_streams"] == 5
    assert saved["debug_logs"] is True
    assert saved["transcoding_profile"] == "auto"
    assert saved["transcoding_video_quality"] == 24


def test_manual_profile_is_rejected(monkeypatch):
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        profiles.set_profile(
            profiles.ProfileBody(
                profile="vaapi-full-h264",
                quality=22,
            )
        )

    assert exc.value.status_code == 400


def test_selected_is_always_auto(monkeypatch):
    monkeypatch.setattr(
        profiles.legacy,
        "read_config",
        lambda: {
            "transcoding_profile": "vaapi-full-h264",
            "transcoding_video_quality": 27,
        },
    )

    selected, quality = profiles._selected()

    assert selected == "auto"
    assert quality == 27


def test_status_normalises_legacy_policy_to_auto(monkeypatch):
    monkeypatch.setattr(
        profiles.base,
        "transcoding_status",
        lambda: {
            "available": True,
            "policy": {
                "transcoding_mode": "hevc",
                "transcoding_hwaccel": "vaapi",
                "transcoding_video_codec": "hevc_vaapi",
            },
            "state": {
                "requested": {
                    "mode": "hevc",
                    "hwaccel": "vaapi",
                    "videoCodec": "hevc_vaapi",
                },
                "effective": {
                    "mode": "hevc",
                    "hwaccel": "vaapi",
                    "videoCodec": "hevc_vaapi",
                },
            },
        },
    )
    monkeypatch.setattr(profiles, "_selected", lambda: ("auto", 22))
    monkeypatch.setattr(profiles, "PROFILE_CACHE", {"at": 0.0, "value": None})

    data = profiles.transcoding_status()

    assert data["executionProfile"] == {"id": "auto", "quality": 22}
    assert data["policy"]["transcoding_mode"] == "auto"
    assert data["policy"]["transcoding_hwaccel"] == "auto"
    assert data["policy"]["transcoding_video_codec"] is None
    assert data["state"]["requested"]["mode"] == "auto"
    assert data["state"]["requested"]["hwaccel"] == "auto"
    assert data["state"]["requested"]["videoCodec"] is None
    assert data["state"]["effective"]["mode"] == "auto"
    assert data["state"]["effective"]["hwaccel"] == "auto"
    assert data["state"]["effective"]["videoCodec"] is None


def test_dashboard_has_no_legacy_execution_selectors():
    dashboard = (ROOT / "webadmin" / "static" / "transcoding-dashboard.js").read_text()

    forbidden = (
        "Force H.264",
        "Force HEVC",
        "Force software",
        "Copy / passthrough",
        "H.264 VAAPI'],",
        "HEVC VAAPI'],",
        "NVIDIA NVENC'],",
    )
    for value in forbidden:
        assert value not in dashboard

    assert "AUTO-only transcoding" in dashboard
    assert "Stremio remains authoritative" in dashboard


def test_dashboard_correlates_playback_by_source_infohash():
    dashboard = (ROOT / "webadmin" / "static" / "index.html").read_text()

    assert "x.sourceInfoHash" in dashboard
    assert "byHash.get(h)" in dashboard
    assert "s.playbackActive=true" in dashboard
    assert "if(!s.playbackActive)return null" in dashboard


def test_dashboard_keeps_legacy_playback_fallback():
    dashboard = (ROOT / "webadmin" / "static" / "index.html").read_text()

    assert "sessions.some(x=>x.sourceInfoHash)" in dashboard
    assert "const active=streams.filter(s=>s.active)" in dashboard
    assert "active.length===1&&sessions.length===1" in dashboard


def test_dashboard_prefers_registry_activity_for_direct_streams():
    dashboard = (ROOT / "webadmin" / "static" / "index.html").read_text()

    assert "classifyPlayback(streams,transcoding,playback)" in dashboard
    assert "playback&&Array.isArray(playback.active)" in dashboard
    assert "let mode='direct'" in dashboard
    assert "s.playbackCount++" in dashboard
    assert "s.playbackModes.push(mode)" in dashboard
    assert "sourceLatest=new Map()" in dashboard
    assert "p.kind!=='source'" in dashboard
    assert "p.userAgent||''" in dashboard
    assert "sourceLatest.set(key,p)" in dashboard
    assert "classifyPlayback(state.streams,transcoding,state.playback)" in dashboard
    assert "registryAvailable=!!(playback&&Array.isArray(playback.active))" in dashboard
    assert "if(registryAvailable){let correlated=0;activity.forEach" in dashboard
    assert "byHash.forEach(s=>{if(s.playbackModes.length)s.playbackMode=s.playbackModes[0]});return" in dashboard


def test_dashboard_correlates_hls_registry_job_to_transcoding_session():
    dashboard = (ROOT / "webadmin" / "static" / "index.html").read_text()

    assert "p.kind==='hls'" in dashboard
    assert "p.workloadId||p.jobId" in dashboard
    assert "sessions.find(x=>x.jobId===workload)" in dashboard


def _run_classify_playback_js(streams, transcoding, playback):
    """Execute the dashboard's real classifyPlayback() function with Node."""
    import json
    import re
    import shutil
    import subprocess
    from pathlib import Path

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")

    dashboard = Path("webadmin/static/index.html").read_text(encoding="utf-8")

    match = re.search(
        r"function classifyPlayback\(streams,transcoding,playback\)\{.*?\}"
        r"(?=\s*function streamHTML)",
        dashboard,
        flags=re.S,
    )
    assert match, "classifyPlayback() not found in dashboard"

    function_src = match.group(0)

    harness = f"""
{function_src}

const streams = {json.dumps(streams)};
const transcoding = {json.dumps(transcoding)};
const playback = {json.dumps(playback)};

classifyPlayback(streams, transcoding, playback);
console.log(JSON.stringify(streams));
"""

    result = subprocess.run(
        [node, "-e", harness],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_playback_empty_registry_does_not_promote_active_torrent_to_playing():
    streams = [{
        "infoHash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "active": True,
    }]

    result = _run_classify_playback_js(
        streams,
        {"active": {"sessions": []}},
        {"active": []},
    )

    # The playback registry exists and explicitly reports no active playback.
    # A torrent may still be active for seeding/download purposes, but that is
    # not evidence that a media player is consuming it.
    assert result[0]["playbackActive"] is False
    assert result[0]["playbackCount"] == 0
    assert result[0]["playbackMode"] is None
    assert result[0]["playbackModes"] == []


def test_playback_correlated_registry_remains_authoritative():
    streams = [
        {
            "infoHash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "active": True,
        },
        {
            "infoHash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "active": True,
        },
    ]

    result = _run_classify_playback_js(
        streams,
        {"active": {"sessions": []}},
        {
            "active": [{
                "kind": "source",
                "infoHash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "fileIdx": 0,
                "client": "web",
                "userAgent": "test",
                "lastActivity": 2,
                "startedAt": 1,
            }]
        },
    )

    assert result[0]["playbackActive"] is True
    assert result[0]["playbackMode"] == "direct"
    assert result[0]["playbackCount"] == 1

    # Registry correlated successfully, so the second merely-active
    # stream must not be promoted by the fallback.
    assert result[1]["playbackActive"] is False
    assert result[1]["playbackCount"] == 0


def test_playback_multiple_active_streams_do_not_inherit_one_ffmpeg_mode():
    streams = [
        {
            "infoHash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "active": True,
        },
        {
            "infoHash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "active": True,
        },
    ]

    transcoding = {
        "active": {
            "sessions": [{
                "jobId": "job-1",
                "sourceInfoHash": None,
                "targetVideo": "h264",
                "targetAudio": "aac",
            }]
        }
    }

    result = _run_classify_playback_js(
        streams,
        transcoding,
        {"active": []},
    )

    assert result[0]["playbackActive"] is False
    assert result[1]["playbackActive"] is False

    assert result[0]["playbackMode"] is None
    assert result[1]["playbackMode"] is None

    assert result[0]["playbackModes"] == []
    assert result[1]["playbackModes"] == []
