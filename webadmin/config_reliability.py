"""WebAdmin configuration reliability fixes.

Loaded last in the WebAdmin extension chain so configuration keys used by the
UI are registered before requests are handled. This keeps the generic
/api/config validator strict while allowing the logging mode exposed by the UI.
"""
from __future__ import annotations

import app as legacy
import transcoding_verified_status as base

# The Logs page persists this key through PUT /api/config.  The UI has exposed
# it for a long time, but it was missing from DEFAULTS, so the strict validator
# returned HTTP 400 ("unknown settings: debug_logs").
legacy.DEFAULTS.setdefault("debug_logs", False)
legacy.DESCRIPTIONS.setdefault(
    "debug_logs",
    "Activa logging detalhado para diagnóstico; requer reinício do servidor.",
)

app = base.app

# Legacy profile state remains registered for upgrade compatibility so older
# persisted values are not treated as unknown settings. AUTO is the only
# supported execution policy, therefore these fields remain read-only.
legacy.DEFAULTS.setdefault("transcoding_profile", "auto")
legacy.DEFAULTS.setdefault("transcoding_resolved_profile", "")
legacy.DESCRIPTIONS.setdefault(
    "transcoding_profile",
    "Modo AUTO; o WebAdmin detecta capacidades e o Stremio decide copy/transcode/codec.",
)
legacy.READ_ONLY.add("transcoding_profile")

# Legacy execution parameters remain read-only. Hardware/runtime verification
# is diagnostic; the generic All Configuration page must not turn old values
# into media-decision controls. Stremio remains authoritative for copy/transcode.
legacy.READ_ONLY.update({
    "transcoding_profile",
    "transcoding_resolved_profile",
    "transcoding_mode",
    "transcoding_hwaccel",
    "transcoding_vaapi_device",
    "transcoding_video_codec",
    "transcoding_hw_decode",
    "transcoding_audio_codec",
    "transcoding_fallback_codec",
})
