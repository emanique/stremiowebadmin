"""WebAdmin addon discovery, management and update integration.

The Library addon ships inside the Stremio server runtime rather than as an independent package.
WebAdmin therefore exposes its install URL and management page, and maps "Update addon" to the
existing guarded server updater. This keeps versioning truthful: updating the addon means updating
the verified server release that contains it.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlsplit
from pathlib import Path

from fastapi.responses import FileResponse, HTMLResponse

import version_lifecycle as lifecycle

STATIC = Path(__file__).with_name("static")
SCRIPT_TAG = '<script src="/addon-connect.js"></script>'


def _readonly_cache_addon_token(kind: str) -> str:
    """Discover a Core-created token via an optional read-only QA cache mount.

    No root Docker socket or container.exec is needed. The Core creates the
    token; WebAdmin can only read it, and never scans arbitrary paths.
    """
    mount = os.environ.get("STREMIO_ADDON_CACHE_RO", "").strip()
    if not mount:
        return ""
    root = Path(mount)
    try:
        if kind == "library":
            path = root / "library-ui.json"
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024:
                return ""
            token = json.loads(path.read_text("utf-8")).get("addon_token", "")
            return token if isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token) else ""
        if kind == "subtitle":
            path = root / ".subtitle-translator" / ".addon-token"
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 128:
                return ""
            token = path.read_text("utf-8").strip()
            return token if re.fullmatch(r"[0-9a-f]{64}", token) else ""
    except (OSError, ValueError, TypeError, AttributeError):
        return ""
    return ""


def _manifest_origin(default: str) -> str:
    """Optional loopback-only QA origin, never an arbitrary external override."""
    requested = os.environ.get("STREMIO_ADDON_ORIGIN", "").rstrip("/")
    if not requested:
        return default
    parsed = urlsplit(requested)
    qa_address = os.environ.get("IPADDRESS", "").strip()
    qa_mode = os.environ.get("STREMIO_ADDON_QA_MODE", "") == "1"
    is_qa_https = (
        qa_mode and qa_address == "192.168.1.250"
        and parsed.scheme == "https"
        and parsed.hostname == qa_address and parsed.port == 18443
    )
    if ((not is_qa_https and (parsed.scheme not in ("http", "https") or
                               parsed.hostname not in ("127.0.0.1", "localhost", "::1")))
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        return default
    return requested


def _server_update_state() -> dict[str, object]:
    """Version/update state of the runtime that owns the Library addon."""
    try:
        versions = lifecycle.component_versions()
        server = versions.get("server") if isinstance(versions, dict) else {}
        server = server if isinstance(server, dict) else {}
        return {
            "mode": "server-runtime",
            "installed": server.get("installed"),
            "available": server.get("available"),
            "updateAvailable": server.get("updateAvailable"),
            "running": bool(server.get("updateInProgress")),
            "endpoint": "/api/addons/library/update",
            "message": "The Library addon is bundled with the Stremio server runtime.",
        }
    except Exception:
        return {
            "mode": "server-runtime",
            "installed": None,
            "available": None,
            "updateAvailable": None,
            "running": False,
            "endpoint": "/api/addons/library/update",
            "message": "Could not verify the server release that contains this addon.",
        }


def _library_addon(update_state: dict[str, object] | None = None) -> dict[str, object]:
    urls = lifecycle._connection_urls()
    trusted = bool(urls.get("trustedHttps"))
    ip = str(urls.get("ip") or "localhost")
    update_state = update_state or _server_update_state()

    try:
        token = _readonly_cache_addon_token("library")
        if not token:
            container = lifecycle.legacy.client().containers.get(lifecycle.legacy.CONTAINER)
            container.reload()
            env = lifecycle._container_env(container)
            cache_root = (env.get("STREMIOSRV_CACHE_ROOT") or "/root/.stremio-server").rstrip("/")
            code = (
                "from stremiosrv.library.session import ensure_addon_token; "
                f"print(ensure_addon_token({json.dumps(cache_root)}))"
            )
            result = container.exec_run(["/srv/app/.venv/bin/python", "-c", code])
            if result.exit_code != 0:
                raise RuntimeError("could not read addon token")
            token = result.output.decode("utf-8", errors="replace").strip()
            if not token:
                raise RuntimeError("empty addon token")
    except Exception as exc:
        return {
            "id": "library",
            "name": "My Library",
            "description": "Cached titles and local streams from this server.",
            "enabled": False,
            "ready": False,
            "manifestUrl": "",
            "libraryUrl": "",
            "managementUrl": "",
            "resources": [],
            "deleteSupported": True,
            "update": update_state,
            "status": f"Unavailable: {type(exc).__name__}",
        }

    manifest = lifecycle.legacy.get_json(f"/library/addon/{token}/manifest.json", None)
    enabled = isinstance(manifest, dict) and bool(manifest.get("id"))

    if trusted:
        origin = str(urls.get("streamingServer") or "").rstrip("/")
    else:
        origin = f"https://{ip}:12470"
    origin = _manifest_origin(origin)
    trusted = trusted and origin.startswith("https://")

    resources: list[str] = []
    if isinstance(manifest, dict):
        for resource in manifest.get("resources", []):
            if isinstance(resource, dict) and resource.get("name"):
                name = str(resource["name"])
                if name not in resources:
                    resources.append(name)

    ready = enabled and trusted
    status = (
        "Ready to install in Stremio"
        if ready
        else "Library addon is disabled or not reachable"
        if not enabled
        else "Addon found, but trusted HTTPS is not available"
    )
    library_url = f"{origin}/library/" if enabled else ""

    return {
        "id": "library",
        "name": str(manifest.get("name") or "My Library") if isinstance(manifest, dict) else "My Library",
        "version": str(manifest.get("version") or "") if isinstance(manifest, dict) else "",
        "description": (
            str(manifest.get("description") or "Cached titles and local streams from this server.")
            if isinstance(manifest, dict)
            else "Cached titles and local streams from this server."
        ),
        "enabled": enabled,
        "ready": ready,
        "manifestUrl": f"{origin}/library/addon/{token}/manifest.json" if enabled else "",
        "libraryUrl": library_url,
        "managementUrl": library_url,
        "resources": resources,
        "trustedHttps": trusted,
        "deleteSupported": True,
        "update": update_state,
        "status": status,
    }



def _subtitle_translator_addon() -> dict[str, object]:
    """Independent addon, without changing My Library's manifest or lifecycle."""
    urls = lifecycle._connection_urls()
    trusted = bool(urls.get("trustedHttps"))
    ip = str(urls.get("ip") or "localhost")
    origin = (str(urls.get("streamingServer") or "").rstrip("/")
              if trusted else f"https://{ip}:12470")
    origin = _manifest_origin(origin)
    trusted = trusted and origin.startswith("https://")
    base = {
        "id": "subtitle-translator",
        "name": "Subtitle Translator",
        "description": "Offline subtitle translation (pt-PT), persisted per source title.",
        "enabled": False,
        "ready": False,
        "manifestUrl": "",
        "managementUrl": "",
        "resources": ["subtitles"],
        "deleteSupported": False,
        "update": {"mode": "server-runtime", "updateAvailable": False,
                   "message": "Developer preview: validation required before release"},
        "status": "Not installed or unavailable",
    }
    try:
        token = _readonly_cache_addon_token("subtitle")
        if token:
            url = f"{origin}/subtitle-translator/{token}/manifest.json"
        else:
            container = lifecycle.legacy.client().containers.get(lifecycle.legacy.CONTAINER)
            container.reload()
            env = lifecycle._container_env(container)
            root = (env.get("STREMIOSRV_CACHE_ROOT") or "/root/.stremio-server").rstrip("/")
            script = (
                "from stremiosrv.subtitle_translator.addon import installed_manifest_url; "
                f"print(installed_manifest_url({json.dumps(root)}, {json.dumps(origin)}))"
            )
            output = container.exec_run(["/srv/app/.venv/bin/python", "-c", script])
            if output.exit_code != 0:
                return base
            url = output.output.decode("utf-8", "replace").strip()
            if not url.startswith(origin + "/subtitle-translator/"):
                return base
            token = url.split("/subtitle-translator/", 1)[1].split("/", 1)[0]
        manifest = lifecycle.legacy.get_json(
            f"/subtitle-translator/{token}/manifest.json", None
        )
        if not isinstance(manifest, dict) or manifest.get("id") != "org.stremiosrv.subtitle-translator":
            return base
        models = lifecycle.legacy.get_json(
            f"/subtitle-translator/{token}/models.json", None
        )
        sources = models.get("sources", []) if isinstance(models, dict) else []
        model_ready = isinstance(sources, list) and len(sources) > 0
        source_ready = bool((models or {}).get("sourceAddonsConfigured"))
        base.update({
            "managementUrl": "",  # old manual SRT page remains QA-only, not the product flow
            "description": (
                "GTSubs-style selectable pt-PT subtitle tracks; offline translation."
            ),
            "version": str(manifest.get("version") or ""),
            "enabled": True,
            "ready": bool(trusted and model_ready and source_ready),
            "manifestUrl": url,
            "trustedHttps": trusted,
            "status": ("QA preview: source discovery and local model configured" if trusted and model_ready and source_ready
                       else "Missing source subtitle addons or offline model" if trusted
                       else "Trusted HTTPS required"),
        })
    except Exception:
        pass
    return base

def _iptv_addon() -> dict[str, object]:
    """Discover the independently bundled IPTV addon without exposing M3U credentials."""
    urls = lifecycle._connection_urls()
    trusted = bool(urls.get("trustedHttps"))
    ip = str(urls.get("ip") or "localhost")
    origin = (str(urls.get("streamingServer") or "").rstrip("/")
              if trusted else f"https://{ip}:12470")
    origin = _manifest_origin(origin)
    trusted = trusted and origin.startswith("https://")
    base: dict[str, object] = {
        "id": "iptv-local",
        "name": "IPTV via StremioSRV",
        "description": "Private M3U/M3U8 channel catalog; settings and channel ordering in IPTV tab.",
        "enabled": False,
        "ready": False,
        "manifestUrl": "",
        "managementUrl": "/#iptv",
        "resources": ["catalog", "meta", "stream"],
        "deleteSupported": False,
        "update": {"mode": "server-runtime", "updateAvailable": False,
                   "message": "The IPTV addon is bundled with the private Stremio runtime."},
        "status": "Configure your own M3U/M3U8 playlist in the IPTV tab.",
    }
    path = Path(os.environ.get("IPTV_CONFIG_FILE", "/config/iptv-local.json"))
    try:
        if path.is_symlink() or not path.is_file():
            return base
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("enabled", True):
            base["status"] = "IPTV addon disabled in the IPTV tab."
            return base
        uploaded = data.get("playlist_source") == "file"
        local_file = Path(os.environ.get("IPTV_UPLOAD_FILE", "/config/iptv-playlist-upload.m3u"))
        if uploaded:
            if local_file.is_symlink() or not local_file.is_file():
                return base
        elif not str(data.get("playlist_url") or "").startswith("https://"):
            return base
        token = data.get("addon_token", "")
        if not isinstance(token, str) or len(token) < 32:
            return base
        # The manifest URL necessarily contains an installation capability.
        # Never include the playlist URL, username or password in this API.
        manifest = lifecycle.legacy.get_json(f"/iptv/{token}/manifest.json", None)
        if not isinstance(manifest, dict) or manifest.get("id") != "org.stremiosrv.iptv.local":
            base["status"] = "IPTV configured; Core addon endpoint unavailable."
            return base
        base.update(
            enabled=True,
            ready=trusted,
            version=str(manifest.get("version") or ""),
            manifestUrl=f"{origin}/iptv/{token}/manifest.json",
            status=("Ready to install in Stremio" if trusted
                    else "Trusted HTTPS required for the install link"),
        )
    except (OSError, ValueError, TypeError):
        base["status"] = "IPTV configuration unavailable or invalid."
    except Exception:
        base["status"] = "IPTV Core endpoint not reachable."
    return base


def addons():
    """Addons currently provided by this server.

    The response shape is intentionally a list so future server addons can be added without
    changing the WebAdmin UI contract.
    """
    update_state = _server_update_state()
    return {"addons": [_library_addon(update_state), _iptv_addon(), _subtitle_translator_addon()]}


def update_library_addon():
    """Update the verified server runtime that contains the Library addon."""
    result = lifecycle.guarded_update()
    if isinstance(result, dict):
        result["addonId"] = "library"
        result["updateMode"] = "server-runtime"
    return result


def addon_script():
    return FileResponse(
        STATIC / "addon-connect.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


def _current_home(app):
    for route in reversed(app.router.routes):
        if getattr(route, "path", None) == "/" and "GET" in (getattr(route, "methods", set()) or set()):
            return route.endpoint
    return None


def install(app) -> None:
    """Install addon API and inject the How to Connect UI into the final WebAdmin stack."""
    original_home = _current_home(app)

    def home_with_addons():
        response = original_home() if original_home else lifecycle.lifecycle_home()
        body = getattr(response, "body", b"")
        text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
        if SCRIPT_TAG not in text:
            text = text.replace("</body>", f"  {SCRIPT_TAG}\n</body>")
        return HTMLResponse(
            text,
            status_code=getattr(response, "status_code", 200),
            headers={"Cache-Control": "no-store"},
        )

    app.router.routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None)
        not in {"/", "/api/addons", "/api/addons/library/update", "/addon-connect.js"}
    ]
    app.add_api_route("/", home_with_addons, methods=["GET"])
    app.add_api_route("/api/addons", addons, methods=["GET"])
    app.add_api_route("/api/addons/library/update", update_library_addon, methods=["POST"], status_code=202)
    app.add_api_route("/addon-connect.js", addon_script, methods=["GET"])
