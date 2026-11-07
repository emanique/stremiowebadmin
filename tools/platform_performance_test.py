#!/usr/bin/env python3
"""End-to-end performance and transcoding-policy diagnostic for the Stremio stack.

Runs from the Docker host and uses only the Python standard library plus the
Docker CLI already required by the project. It intentionally avoids printing
container environments, VPN credentials, tokens, certificates or addon URLs.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SERVER = "http://127.0.0.1:11470"
DEFAULT_WEBADMIN = "http://127.0.0.1:8090"
DEFAULT_PIHOLE = "http://127.0.0.1:8053"
STREMIO_CONTAINER = "stremio-libtorrent-server"
WEBADMIN_CONTAINER = "stremio-webadmin"
PIHOLE_CONTAINER = "stremio-pihole"
GLUETUN_CONTAINER = "stremio-gluetun"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    metrics: dict[str, Any]


def run(argv: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def docker_exists(name: str) -> bool:
    result = run(["docker", "inspect", name], timeout=5)
    return result.returncode == 0


def docker_state(name: str) -> dict[str, Any]:
    if not docker_exists(name):
        return {"exists": False}
    result = run([
        "docker", "inspect", "--format",
        "{{json .State}}", name,
    ], timeout=5)
    try:
        state = json.loads(result.stdout.strip()) if result.returncode == 0 else {}
    except json.JSONDecodeError:
        state = {}
    return {
        "exists": True,
        "running": bool(state.get("Running")),
        "status": state.get("Status"),
        "health": (state.get("Health") or {}).get("Status"),
        "restartCount": state.get("Restarting"),
    }


def docker_stats(name: str) -> dict[str, str]:
    if not docker_exists(name):
        return {}
    fmt = "{{json .}}"
    result = run(["docker", "stats", "--no-stream", "--format", fmt, name], timeout=10)
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        raw = json.loads(result.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return {}
    return {
        "cpu": raw.get("CPUPerc", ""),
        "memory": raw.get("MemUsage", ""),
        "memoryPercent": raw.get("MemPerc", ""),
        "netIO": raw.get("NetIO", ""),
        "blockIO": raw.get("BlockIO", ""),
        "pids": raw.get("PIDs", ""),
    }


def http_get(url: str, timeout: float = 5.0) -> tuple[int, bytes, float]:
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"User-Agent": "stremio-performance-test/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, body, time.perf_counter() - started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), time.perf_counter() - started


def json_get(url: str, timeout: float = 8.0) -> tuple[int, dict[str, Any] | None, float]:
    code, body, elapsed = http_get(url, timeout=timeout)
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    return code, payload if isinstance(payload, dict) else None, elapsed


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[idx]


def load_test(name: str, url: str, requests: int, concurrency: int) -> Check:
    latencies: list[float] = []
    codes: dict[int, int] = {}
    errors = 0
    started = time.perf_counter()

    def one(_: int) -> tuple[int, float]:
        try:
            code, _, elapsed = http_get(url, timeout=8)
            return code, elapsed
        except Exception:
            return 0, 0.0

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        for code, elapsed in pool.map(one, range(requests)):
            if code:
                codes[code] = codes.get(code, 0) + 1
                latencies.append(elapsed)
            else:
                errors += 1

    duration = max(time.perf_counter() - started, 0.0001)
    success = sum(count for code, count in codes.items() if 200 <= code < 400)
    status = "PASS" if success == requests else "WARN" if success > 0 else "FAIL"
    detail = f"{success}/{requests} successful; {requests / duration:.1f} req/s"
    metrics = {
        "requests": requests,
        "concurrency": concurrency,
        "durationSeconds": round(duration, 3),
        "requestsPerSecond": round(requests / duration, 2),
        "httpCodes": codes,
        "errors": errors,
        "latencyMs": {
            "min": round(min(latencies) * 1000, 2) if latencies else None,
            "mean": round(statistics.mean(latencies) * 1000, 2) if latencies else None,
            "p50": round((percentile(latencies, 0.50) or 0) * 1000, 2) if latencies else None,
            "p95": round((percentile(latencies, 0.95) or 0) * 1000, 2) if latencies else None,
            "p99": round((percentile(latencies, 0.99) or 0) * 1000, 2) if latencies else None,
            "max": round(max(latencies) * 1000, 2) if latencies else None,
        },
    }
    return Check(name, status, detail, metrics)


def container_check(name: str) -> Check:
    state = docker_state(name)
    stats = docker_stats(name) if state.get("running") else {}
    if not state.get("exists"):
        return Check(f"container:{name}", "WARN", "container not present", state)
    if not state.get("running"):
        return Check(f"container:{name}", "FAIL", f"state={state.get('status')}", state)
    health = state.get("health")
    status = "PASS" if health in {None, "healthy"} else "WARN"
    return Check(f"container:{name}", status, f"running; health={health or 'n/a'}", {**state, **stats})


def dns_check() -> Check:
    if not docker_exists(STREMIO_CONTAINER):
        return Check("dns-path", "FAIL", "Stremio container not found", {})
    started = time.perf_counter()
    result = run(["docker", "exec", STREMIO_CONTAINER, "getent", "hosts", "example.com"], timeout=10)
    elapsed = time.perf_counter() - started
    if result.returncode == 0 and result.stdout.strip():
        return Check("dns-path", "PASS", "name resolution succeeded from Stremio namespace", {"latencyMs": round(elapsed * 1000, 2)})
    return Check("dns-path", "FAIL", "name resolution failed from Stremio namespace", {"stderr": result.stderr[-300:]})


def gluetun_check() -> Check:
    if not docker_exists(GLUETUN_CONTAINER):
        return Check("gluetun", "WARN", "VPN mode is not running", {})
    state = docker_state(GLUETUN_CONTAINER)
    status = "PASS" if state.get("running") and state.get("health") == "healthy" else "FAIL"
    return Check("gluetun", status, f"state={state.get('status')} health={state.get('health')}", state)


def transcoding_api_check(webadmin: str) -> tuple[Check, dict[str, Any] | None]:
    code, data, elapsed = json_get(f"{webadmin.rstrip('/')}/api/transcoding/status", timeout=10)
    if code != 200 or not data:
        return Check("transcoding-status", "FAIL", f"HTTP {code}; no JSON status", {"latencyMs": round(elapsed * 1000, 2)}), data

    runtime = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
    execution = data.get("executionProfile") if isinstance(data.get("executionProfile"), dict) else {}
    telemetry = data.get("policyTelemetry") if isinstance(data.get("policyTelemetry"), dict) else {}
    warnings = data.get("warnings") if isinstance(data.get("warnings"), list) else []
    ready = bool(runtime.get("ready"))
    profile = execution.get("id")
    if not ready:
        status = "FAIL"
    elif profile in {None, "", "legacy"}:
        status = "WARN"
    else:
        status = "PASS"
    detail = f"runtimeReady={ready}; profile={profile}; latestDecisionDetected={bool(telemetry.get('detected'))}"
    return Check("transcoding-status", status, detail, {
        "latencyMs": round(elapsed * 1000, 2),
        "runtimeReady": ready,
        "profile": profile,
        "policySummary": data.get("policySummary"),
        "policyTelemetry": telemetry,
        "warnings": warnings,
        "active": data.get("active"),
        "hardware": data.get("hardware"),
    }), data


def transcoding_profiles_check(webadmin: str) -> Check:
    url = f"{webadmin.rstrip('/')}/api/transcoding/profiles/refresh"
    started = time.perf_counter()
    request = urllib.request.Request(url, method="POST", headers={"User-Agent": "stremio-performance-test/1"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = response.read()
            code = response.status
    except Exception as exc:
        return Check("transcoding-profiles", "FAIL", f"profile runtime tests failed: {exc}", {})
    elapsed = time.perf_counter() - started
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        data = {}
    profiles = data.get("profiles") if isinstance(data, dict) and isinstance(data.get("profiles"), list) else []
    available = [str(item.get("id")) for item in profiles if isinstance(item, dict) and item.get("available")]
    status = "PASS" if code == 200 and available else "WARN"
    return Check("transcoding-profiles", status, f"verified profiles: {', '.join(available) if available else 'none'}", {
        "latencyMs": round(elapsed * 1000, 2),
        "availableProfiles": available,
        "selected": data.get("selected") if isinstance(data, dict) else None,
        "device": data.get("device") if isinstance(data, dict) else None,
        "ffmpeg": data.get("ffmpeg") if isinstance(data, dict) else None,
    })


def wrapper_policy_self_test() -> Check:
    if not docker_exists(STREMIO_CONTAINER):
        return Check("transcoding-policy-self-test", "FAIL", "Stremio container not found", {})
    argv = [
        "docker", "exec", STREMIO_CONTAINER,
        "/usr/local/bin/ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
        "-frames:v", "3", "-c:v", "libx264", "-f", "null", "-",
    ]
    started = time.perf_counter()
    result = run(argv, timeout=60)
    elapsed = time.perf_counter() - started
    combined = "\n".join([result.stdout, result.stderr])
    policy_lines = [line.strip() for line in combined.splitlines() if "[ffmpeg-policy]" in line]
    if result.returncode != 0:
        return Check("transcoding-policy-self-test", "FAIL", "synthetic FFmpeg job failed", {
            "elapsedSeconds": round(elapsed, 3),
            "lastOutput": combined[-500:],
        })
    if not policy_lines:
        return Check("transcoding-policy-self-test", "FAIL", "FFmpeg ran but wrapper emitted no policy decision", {
            "elapsedSeconds": round(elapsed, 3),
        })
    # Policy lines contain only execution decisions; they do not expose secrets.
    return Check("transcoding-policy-self-test", "PASS", policy_lines[-1], {
        "elapsedSeconds": round(elapsed, 3),
        "policyLine": policy_lines[-1],
    })


def ffmpeg_benchmark(seconds: int) -> Check:
    if not docker_exists(STREMIO_CONTAINER):
        return Check("ffmpeg-benchmark", "FAIL", "Stremio container not found", {})
    frames = max(24, seconds * 24)
    argv = [
        "docker", "exec", STREMIO_CONTAINER,
        "/usr/local/bin/ffmpeg", "-hide_banner", "-loglevel", "error", "-benchmark",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24",
        "-frames:v", str(frames), "-c:v", "libx264", "-f", "null", "-",
    ]
    before = docker_stats(STREMIO_CONTAINER)
    started = time.perf_counter()
    result = run(argv, timeout=max(60, seconds * 10))
    elapsed = time.perf_counter() - started
    after = docker_stats(STREMIO_CONTAINER)
    realtime_factor = seconds / elapsed if elapsed > 0 else 0
    status = "PASS" if result.returncode == 0 and realtime_factor >= 1.0 else "WARN" if result.returncode == 0 else "FAIL"
    return Check("ffmpeg-benchmark", status, f"{seconds}s synthetic 720p encoded in {elapsed:.2f}s ({realtime_factor:.2f}x realtime)", {
        "sourceSeconds": seconds,
        "elapsedSeconds": round(elapsed, 3),
        "realtimeFactor": round(realtime_factor, 3),
        "containerStatsBefore": before,
        "containerStatsAfter": after,
        "exitCode": result.returncode,
    })


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end Stremio platform performance test")
    parser.add_argument("--server", default=os.getenv("PERF_SERVER_URL", DEFAULT_SERVER))
    parser.add_argument("--webadmin", default=os.getenv("PERF_WEBADMIN_URL", DEFAULT_WEBADMIN))
    parser.add_argument("--pihole", default=os.getenv("PERF_PIHOLE_URL", DEFAULT_PIHOLE))
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--benchmark-seconds", type=int, default=5)
    parser.add_argument("--skip-ffmpeg-benchmark", action="store_true")
    parser.add_argument("--json-out", default="performance-report.json")
    args = parser.parse_args()

    checks: list[Check] = []
    for container in (STREMIO_CONTAINER, WEBADMIN_CONTAINER, PIHOLE_CONTAINER):
        checks.append(container_check(container))
    if docker_exists(GLUETUN_CONTAINER):
        checks.append(container_check(GLUETUN_CONTAINER))
        checks.append(gluetun_check())

    checks.append(load_test("server-health-load", f"{args.server.rstrip('/')}/health", args.requests, args.concurrency))
    checks.append(load_test("webadmin-health-load", f"{args.webadmin.rstrip('/')}/health", args.requests, args.concurrency))
    checks.append(load_test("pihole-web-load", f"{args.pihole.rstrip('/')}/admin/", max(20, args.requests // 2), min(args.concurrency, 5)))
    checks.append(dns_check())

    transcode_status, _ = transcoding_api_check(args.webadmin)
    checks.append(transcode_status)
    checks.append(transcoding_profiles_check(args.webadmin))
    checks.append(wrapper_policy_self_test())
    if not args.skip_ffmpeg_benchmark:
        checks.append(ffmpeg_benchmark(max(1, args.benchmark_seconds)))

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "host": {"python": sys.version.split()[0]},
        "configuration": {
            "requests": args.requests,
            "concurrency": args.concurrency,
            "benchmarkSeconds": 0 if args.skip_ffmpeg_benchmark else args.benchmark_seconds,
        },
        "checks": [asdict(check) for check in checks],
        "summary": {
            "pass": sum(check.status == "PASS" for check in checks),
            "warn": sum(check.status == "WARN" for check in checks),
            "fail": sum(check.status == "FAIL" for check in checks),
        },
    }

    output = Path(args.json_out)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("\nStremio platform performance test")
    print("=" * 72)
    for check in checks:
        print(f"{check.status:4}  {check.name:32} {check.detail}")
    print("-" * 72)
    print(f"PASS={report['summary']['pass']} WARN={report['summary']['warn']} FAIL={report['summary']['fail']}")
    print(f"JSON report: {output}")

    return 1 if report["summary"]["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
