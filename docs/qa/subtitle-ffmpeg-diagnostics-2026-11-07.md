# Embedded subtitle diagnostics: acceptance (2026-11-07)

## Observed issue

The operational Stremio host `192.168.1.199` showed repeated
`embedded subtitle ffmpeg exited with code 1` warnings during media
playback. Nearby logs also reported torrent pieces unavailable for
30 seconds. The correlation does **not** prove that missing pieces cause
every subtitle failure; previously the FFmpeg stderr was sent to
`subprocess.DEVNULL`.

## Technical change

- Retain FFmpeg stderr in a private temporary file, avoiding stderr PIPE
  deadlocks while subtitle output is streamed over stdout.
- Emit only a fixed enumerated cause, such as
  `incomplete_or_invalid_media`, `subtitle_decode_error`,
  `subtitle_stream_unavailable`, `source_timeout`,
  `source_http_error`, or `unclassified`.
- Never log raw stderr, torrent URL, file path, tracker, stream token, or
  private media identifiers. The temporary diagnostic file is closed after
  the extraction process terminates.
- Preserve the previous API contract, FFmpeg invocation, return code and
  HLS WebVTT caching/streaming behaviour.

## QA evidence

- Targeted subtitle route and diagnostic tests: 36 passed.
- Real FFmpeg 7.1.5 synthetic Matroska/SubRip -> WebVTT extraction: PASS.
- Frozen Python 3.12 full project regression:
  **1,595 passed, 2 skipped, 0 failed**, 3 warnings;
  `--basetemp` located on disk-backed `/var/tmp`.
- No update/restart performed on the operational Stremio host.

## Unresolved acceptance

This change improves evidence but **does not yet fix** a failure to
extract subtitles from an incomplete remote/torrent source.
Repeat playback on a fully downloaded test file, compare embedded
subtitle codecs and language tracks, and use the new bounded error reason
to distinguish missing torrent pieces from decoder/probe issues.
Conduct IPTV/TV-client playback and subtitle testing before stable release.
