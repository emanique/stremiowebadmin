"""GPU backend must be an explicitly configured internal-only service."""
import pytest
from stremiosrv.ai_subtitles.generator import GenerationUnavailable, run_openvino_bridge


def test_bridge_does_not_allow_external_endpoints(tmp_path):
    with pytest.raises(GenerationUnavailable, match="internal service"):
        run_openvino_bridge(tmp_path, "http://external.invalid:19098", "a" * 64)


def test_bridge_requires_secret(tmp_path):
    with pytest.raises(GenerationUnavailable, match="credentials missing"):
        run_openvino_bridge(tmp_path, "http://ai-openvino:19098", "")


def test_backend_off_does_not_require_shared_volume(tmp_path, monkeypatch):
    from stremiosrv.ai_subtitles.generator import generate
    monkeypatch.delenv("STREMIOSRV_AI_OPENVINO_URL", raising=False)
    with pytest.raises(GenerationUnavailable, match="completion"):
        generate(tmp_path / "media.mkv", tmp_path, "a" * 40, 0)


def test_openvino_http_error_keeps_bounded_server_reason(tmp_path):
    import io
    import urllib.error
    from unittest.mock import patch
    body = b'{"error":"OpenVINO inference failed: GPU unavailable"}'
    error = urllib.error.HTTPError(
        "http://ai-openvino:19098/run", 500, "Internal Server Error", {}, io.BytesIO(body))
    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(GenerationUnavailable, match="OpenVINO HTTP 500: OpenVINO inference failed: GPU unavailable"):
            run_openvino_bridge(tmp_path, "http://ai-openvino:19098", "a" * 64)


def test_openvino_http_error_rejects_non_json_and_keeps_status(tmp_path):
    import io
    import urllib.error
    from unittest.mock import patch
    error = urllib.error.HTTPError("http://ai-openvino:19098/run", 409,
                                   "Conflict", {}, io.BytesIO(b'not-json'))
    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(GenerationUnavailable, match=r"OpenVINO HTTP 409$"):
            run_openvino_bridge(tmp_path, "http://ai-openvino:19098", "a" * 64)
