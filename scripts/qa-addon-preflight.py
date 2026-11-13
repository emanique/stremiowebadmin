#!/usr/bin/env python3
"""Read-only preflight for two Stremio addons on a nominated QA host.

Never attempts installation, credentials, API mutations or manifest-token
printing. A Stremio-addon manifest is an install capability; do not log it.
Uses HTTPS without disabling certificate validation by default.
"""
from __future__ import annotations

import argparse
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request


def _get(base: str, path: str, *, insecure: bool = False) -> tuple[int, object]:
    context = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    try:
        with urllib.request.urlopen(
            urllib.request.Request(base.rstrip("/") + path, headers={
                "Accept": "application/json", "User-Agent": "StremioAddonQA/1.0"
            }), context=context, timeout=8
        ) as response:
            payload = response.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                raise ValueError("response exceeds 1 MiB")
            if "application/json" not in response.headers.get("Content-Type", ""):
                return response.status, {}
            return response.status, json.loads(payload)
    except urllib.error.HTTPError as error:
        return error.code, {}


def probe(base: str, *, insecure: bool = False) -> dict:
    results: dict = {"target": base, "tests": {}}
    for path, label in [
        ("/health", "webadmin_health"),
        ("/api/addons", "addons_list"),
        ("/api/iptv", "iptv_management"),
    ]:
        try:
            status, payload = _get(base, path, insecure=insecure)
            entry: dict = {"http": status, "ok": status == 200}
            if label == "addons_list" and status == 200 and isinstance(payload, dict):
                addons = payload.get("addons", [])
                if isinstance(addons, list):
                    entry["items"] = [
                        {"id": x.get("id"), "enabled": bool(x.get("enabled")),
                         "ready": bool(x.get("ready")), "manifestPresent": bool(x.get("manifestUrl")),
                         "managementPresent": bool(x.get("managementUrl"))}
                        for x in addons if isinstance(x, dict)
                    ]
            if label == "iptv_management" and status == 200 and isinstance(payload, dict):
                for field in ("configured", "enabled", "channels", "sourceType"):
                    if field in payload:
                        entry[field] = payload[field]
            results["tests"][label] = entry
        except (ValueError, urllib.error.URLError, TimeoutError, OSError) as exc:
            results["tests"][label] = {"ok": False, "errorType": type(exc).__name__}
    addons = results["tests"].get("addons_list", {}).get("items", [])
    by_id = {x["id"]: x for x in addons if isinstance(x.get("id"), str)}
    results["iptvRegistered"] = "iptv-local" in by_id
    results["subtitleTranslatorRegistered"] = "subtitle-translator" in by_id
    results["newAddonsReadyForClientTesting"] = bool(
        by_id.get("iptv-local", {}).get("ready") and
        by_id.get("subtitle-translator", {}).get("ready")
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Base HTTPS WebAdmin URL")
    parser.add_argument("--insecure-lab-cert", action="store_true",
                        help="Allow untrusted LAB TLS certificate; never production")
    args = parser.parse_args()
    parsed = urllib.parse.urlsplit(args.url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        parser.error("a HTTPS WebAdmin URL without embedded credentials is required")
    print(json.dumps(probe(args.url, insecure=args.insecure_lab_cert),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
