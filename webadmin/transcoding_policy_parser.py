"""Parser for FFmpeg policy telemetry.

Kept dependency-free so it can be regression-tested without starting WebAdmin.
"""
from __future__ import annotations

import re


def parse_policy_log(text: str) -> dict[str, object] | None:
    """Parse both current profile-based and legacy mode-based wrapper records."""
    policy_lines = [
        line for line in text.replace("\r", "\n").splitlines()
        if "[ffmpeg-policy]" in line
    ]
    if not policy_lines:
        return None

    line = policy_lines[-1]
    match = re.search(r"\[ffmpeg-policy\]\s+(.*)$", line)
    if not match:
        return {"raw": line, "decision": line}

    payload = match.group(1).strip()
    result: dict[str, object] = {"raw": line, "decision": payload}

    profile_match = re.search(r"(?:^|;)\s*profile=([^;\s]+)", payload)
    mode_match = re.search(r"(?:^|;)\s*mode=([^;\s]+)", payload)
    if profile_match:
        result["profile"] = profile_match.group(1)
    if mode_match:
        result["mode"] = mode_match.group(1)

    # Profile records describe the encoder chosen by the upstream/core and the
    # execution-profile encoder. The left-hand value is not necessarily the
    # source-media codec; source codecs are read separately from FFmpeg logs.
    video_change = re.search(r"(?:^|;)\s*video=([^;\s]+)->([^;\s]+)", payload)
    if video_change:
        result["upstreamVideoTarget"] = video_change.group(1)
        result["targetVideo"] = video_change.group(2)

    audio_change = re.search(r"(?:^|;)\s*audio=([^;\s]+)->([^;\s]+)", payload)
    if audio_change:
        result["upstreamAudioTarget"] = audio_change.group(1)
        result["targetAudio"] = audio_change.group(2)

    if re.search(r"(?:^|;)\s*video=copy\s+preserved", payload):
        result["targetVideo"] = "copy"
        result["action"] = "direct-stream"

    if re.search(r"no video codec option found", payload, re.IGNORECASE):
        result["action"] = "no-video-codec"

    unavailable = re.search(r"unavailable encoder\s+([^;\s]+)", payload)
    if unavailable:
        result["unavailableEncoder"] = unavailable.group(1)
        result["action"] = "fallback-preserve-core"

    return result
