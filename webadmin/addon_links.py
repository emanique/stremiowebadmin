"""WebAdmin addon discovery, management and update integration.

The Library addon ships inside the Stremio server runtime rather than as an independent package.
WebAdmin therefore exposes its install URL and management page, and maps "Update addon" to the
existing guarded server updater. This keeps versioning truthful: updating the addon means updating
the verified server release that contains it.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.responses import FileResponse, HTMLResponse

import version_lifecycle as lifecycle

STATIC = Path(__file__).with_name("static")
SCRIPT_TAG = '<script src="/addon-connect.js"></script>'


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


def addons():
    """Addons currently provided by this server.

    The response shape is intentionally a list so future server addons can be added without
    changing the WebAdmin UI contract.
    """
    update_state = _server_update_state()
    return {"addons": [_library_addon(update_state)]}


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
