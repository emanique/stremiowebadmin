# Native Desktop QA — AI to PT (no production deployment)

## Goal and scope

Verify official Stremio Desktop v5 loads the QA `.201` WebUI using its
`--webui-url` CLI flag (supported by the official stremio-shell-ng). This may
reuse the existing WebPlayer AI selection/clock hook while retaining native
MPV playback. It has NOT been verified end-to-end on Windows.

The QA WebUI is `https://192-168-1-201.519b6502d940.stremio.rocks:12470/`.
No core image, video streaming, transcoding or addon manifest changes are
needed for this launcher experiment. The official UI and default launch are
unchanged after closing the QA instance and starting Stremio normally.

## Procedure

1. Fully close Stremio Desktop.
2. Open PowerShell and run this script (normal user, not administrator):
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-stremio-native-qa.ps1`
3. Verify the UI opens, the video uses the `.201` server, and native Direct Play
   remains selected. The custom WebUI option does not by itself configure the
   streaming-server endpoint: verify that separately.
4. Select AI to PT during playback, check `.201` AI job status, first actual
   pt-PT cue, seek/resume, OFF, and repeat with two independent clients.
5. If the launcher is not supported or the shell cannot load the QA WebUI,
   stop the test and return to normal launch. Never modify the official
   Stremio installation to work around a failed shell handshake.

## Acceptance criteria / rollback

- AI selection starts only the selected session; manifest/track prefetch never
  starts Whisper; native Direct Play persists.
- Playback-time heartbeat matches actual client time; no HLS offset applied
  to native Direct Play; native subtitles update without double rendering.
- OFF cancels only its session; the shared hash cache survives and is reusable.
- Two clients at distinct times remain independent; deleting a title evicts
  all its AI/translation cache and stops associated sessions.
- Restore the default Desktop UI by closing this test instance and launching
  Stremio normally, without `--webui-url`.

## Risks

The remote `.201` WebUI uses the WebPlayer-specific selection/clock hook and
may not receive expected events in the Desktop shell; the presence of the
selected subtitle track alone does not prove success. The desktop shell may
render subtitles in MPV instead of the WebUI, or keep its native local server.
These conditions must be observed, not assumed.
