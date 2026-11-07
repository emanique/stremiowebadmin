# Playback paths: App Direct versus WebPlayer HLS

Reference baseline: emmanique/stremio-webadmin main
68135d08723f4350728fda497c9ac8ba6b44e405 (Core 1.6.34).

## App: Direct playback
App -> Core GET /{infoHash}/{fileIndex} -> playback.serve ->
torrent wait_and_read -> HTTP 206 byte ranges of the original media.
Seek changes Range, refocuses piece priorities. No HLS converter/FFmpeg
in this path. Direct playback differs from a Direct/VPN network route.

## WebPlayer: HLS path
WebPlayer -> /hlsv2/probe -> ffprobe -> /hlsv2/{jobId}/master.m3u8
with videoCodecs, audioCodecs, maxWidth and startTime.
Core fingerprint decides copy/transcode from supported codec, width, HDR.
Core applies HLS direct-copy allowlist and 4K Main10 browser compatibility.
Converter.ensure_job -> build_hls_cmd -> FFmpeg remux or H.264 encode,
master playlist, alternate audio renditions, segments.
Closing the logical player calls /hlsv2/{jobId}/destroy; idle GC cleans jobs.

## Contract
Core is the sole authority deciding HLS video copy versus transcode.
In AUTO the FFmpeg wrapper preserves the Core video-codec decision.
The default HLS copy allowlist is H.264; unsupported source codecs are
explicitly converted by Core. Operator-expanded allowlists are honored.
The native App direct byte-range endpoint is unaffected.

## Observed incident and validation, 2026-11-06
WebPlayer asked for HEVC 3840x1600 10-bit SDR at startTime=372000.
Previously Core chose copy, but AUTO wrapper silently rewrote it as
full VAAPI decode+H.264 encode. FFmpeg exited code 1:
Error reinitializing filters / Function not implemented.

Current HEVC Main10 4K HLS output is explicit H.264 at up to 1920 width.
Real-file check: WebPlayer master, variant and first .ts segment HTTP 200,
H.264 1920x800; App Direct Range responded HTTP 206 with 1024 bytes.
Intel VAAPI and the BitTorrent port were preserved.
This does not yet certify full UI playback/seek/subtitle/resume acceptance.

## Release gates
Verify browser playback for 5+ minutes and repeated seek at 372s,
audio changes, subtitle paths, Continue Watching, no FFmpeg leaks,
App Direct unchanged, and no regressions with H.264 or HEVC 8-bit.
Keep WebAdmin TLS and streaming TLS independent.
