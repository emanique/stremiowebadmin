# Platform performance and transcoding diagnostic

The repository includes `tools/platform_performance_test.py`, an end-to-end host-side test for the Stremio stack. It uses the Docker CLI and Python standard library only and does not print container environments, VPN credentials, tokens, private keys, certificates or Library addon URLs.

## What it tests

The test covers:

- Docker state and health of Stremio, WebAdmin, Pi-hole and Gluetun when VPN mode is active.
- CPU, memory, network I/O, block I/O and process counts for the main containers.
- concurrent HTTP latency and throughput for the Stremio health endpoint, WebAdmin and Pi-hole UI.
- DNS resolution from the Stremio network namespace.
- Gluetun health when VPN mode is active.
- WebAdmin transcoding status and runtime readiness.
- real transcoding profile self-tests through `/api/transcoding/profiles/refresh`.
- FFmpeg policy-wrapper instrumentation with a synthetic media job.
- an optional 720p synthetic FFmpeg benchmark to measure real-time encoding capacity.
- generation of a JSON report suitable for before/after comparisons.

## Run the full test

From the repository directory on the Docker host:

```bash
python3 tools/platform_performance_test.py
```

The default load is 100 requests at concurrency 10 and a 5-second 720p synthetic FFmpeg benchmark.

A heavier test can be run with:

```bash
python3 tools/platform_performance_test.py \
  --requests 500 \
  --concurrency 25 \
  --benchmark-seconds 15 \
  --json-out performance-report-full.json
```

To test the platform without generating an FFmpeg load:

```bash
python3 tools/platform_performance_test.py --skip-ffmpeg-benchmark
```

## Interpreting the result

Each check returns `PASS`, `WARN` or `FAIL`.

Useful thresholds for this platform are:

- Stremio `/health`: no failed requests under the configured concurrency.
- WebAdmin `/health`: no failed requests and stable p95 latency.
- DNS path: successful lookup from the Stremio namespace.
- Gluetun: `healthy` in VPN mode.
- Transcoding runtime: `runtimeReady=true`.
- Transcoding profile: must not remain `legacy` if the operator expects a selected execution profile.
- Policy self-test: must produce one `[ffmpeg-policy]` record.
- FFmpeg 720p benchmark: `>= 1.0x` real-time is the minimum for one stream; additional headroom is recommended for simultaneous streams.

The JSON report contains exact latency percentiles, RPS, container resources, verified transcoding profiles, active policy summary and the synthetic policy decision.

## Why policy decisions previously appeared missing

There were two independent behaviours:

1. The current FFmpeg wrapper emits profile-based telemetry such as:

   ```text
   [ffmpeg-policy] profile=vaapi-h264; video=libx264->h264_vaapi; decode=software; engine=vaapi
   ```

   The Dashboard parser still expected the older `mode=...` telemetry format. The wrapper could therefore be working while `latestDecision` and `policyDecision` appeared empty.

2. The current execution model does **not** invent a transcoding requirement. Stremio remains responsible for deciding whether a stream is Direct Stream or requires transcoding. The selected verified profile only replaces the video execution pipeline when Stremio has already requested a transcode. A `copy` decision is intentionally preserved.

The parser now understands both `profile=...` and legacy `mode=...` records. Source-media codecs continue to be taken from FFmpeg `Stream #` lines; the left side of `video=<old>-><new>` in a profile record is the upstream encoder target, not necessarily the media source codec.

## Direct diagnostic commands

Check WebAdmin telemetry:

```bash
curl -fsS http://127.0.0.1:8090/api/transcoding/status | python3 -m json.tool
```

Force the real runtime encoder tests:

```bash
curl -fsS -X POST http://127.0.0.1:8090/api/transcoding/profiles/refresh | python3 -m json.tool
```

Verify the wrapper directly:

```bash
docker exec stremio-libtorrent-server \
  /usr/local/bin/ffmpeg -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=320x180:rate=24 \
  -frames:v 3 -c:v libx264 -f null -
```

The output must contain an `[ffmpeg-policy]` line. If it does not, the FFmpeg wrapper is not active in the running server image.
