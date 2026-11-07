# Development and release testing (local-first)

Normal development runs exclusively on the DEV server; GitHub Actions is
reserved for manual final release gates, not push/PR jobs.

## Static and unit checks
```bash
cd StremioServerWebAdmin
sh scripts/qa-local.sh
```

This detects host IPv4, validates the Compose model and launcher, then runs
all Python pytest tests in the pinned local venv.

On DEV `192.168.1.221`, `/tmp` is a 3.8 GiB memory-backed tmpfs.
Use a disk-backed TMPDIR. On 2026-11-05 the complete suite yielded
**1514 passed, 2 skipped, 3 warnings** in 210.55 seconds.

## Runtime smoke
```bash
sh start.sh up -d --no-build
bash scripts/full-system-test.sh
```

These checks verify only two containers, bridge networking without a
gateway container, loopback-only WebAdmin, HTTP/HTTPS/JSON endpoints and
normal Docker DNS resolution.

## Pending release tests
- Actual Stremio App, Web Player, torrent and player-state integration
- HLS remux, CPU/VAAPI transcode with seek, audio, embedded subtitles
- My Library, Continue Watching and state persistence after reboot
- Direct BitTorrent TCP and UDP inbound validation (subject to router/NAT)
- WebAdmin authentication, Docker privilege reduction, CSRF protections
- Fresh installation on another server and rollback between releases
- Versioned image publishing to GHCR by immutable digest

These gates must pass before promoting `develop` to `main`.
