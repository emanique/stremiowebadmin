# Controlled upgrade and fresh-install checks

This procedure is generic. Configure the host name/address from the actual
machine, DNS, certificates and network; no server address or VPN endpoint is
baked into this source release.

## Before upgrading

1. Record the current Git/release revision, image digests, effective HTTP/HTTPS
   host ports, BitTorrent listen port, DNS, transcoding/GPU backend and relevant
   Compose overrides. Record the currently mounted cache/configuration volumes.
2. Verify a restorable backup of the current `.env`, host Nginx certificates,
   `/config` WebAdmin/IPTV settings and persistent download cache. Store backups
   away from the deployment directory with restricted access. Do not include
   backups in Git.
3. Check available disk capacity for image layers, torrent cache and optional
   subtitle model staging. Do not assume compressed or deduplicated sizes.
4. Validate the new Compose configuration and DNS resolution before changing
   the running containers. Review firewall exposure: management HTTPS must be
   restricted to a trusted LAN or protected by host authentication.

## Install / upgrade

```sh
# Repository root, after configuring .env for the actual host:
sh start.sh config --quiet
sh start.sh up -d --build
```

When installing a package instead of the source checkout, use the matching
release `.deb`, then run the packaged setup command. The setup wizard manages
host network, certificates and reconciler services. Optional model packages
are installed separately as described in `README.md`.

**Do not use `docker compose down -v`**. Never delete the download/cache
volume or configuration volume as a step of a normal upgrade.

## Acceptance

- Core, WebAdmin and HTTPS WebPlayer return healthy status; original playback
  works with a small file before activating extra addons.
- The same-account My Library addon appears for a verified cached title.
  Source identity is resolved by metadata and file index, not by filename
  similarity; the WebPlayer falls back to stream selection if ambiguous.
- IPTV playlist testing, refresh, searching and client playback operate
  without restarting the Core for each playlist change.
- The Subtitle Translator discovers the installed models. Select an original
  language track, verify a pt-PT SRT and a later cached SRT hit. Confirm the
  bilingual initial notice and source-language indication.
- Verify HTTP Range, HLS, audio, seek, resume and subtitle timing in both
  native Stremio App and the bundled WebPlayer. WebPlayer auto-resume does not
  imply equivalent native App navigation.
- Test reboot persistence, TLS and optional GPU acceleration, then inspect
  error logs, process counts and storage usage.

## Rollback

If acceptance fails, stop the new services, restore the previous immutable
images/Compose revision and preserved `.env`, and re-enable the previous
services without removing volumes. Restore data backups only if data itself
was altered; never overwrite newer user files with an older backup casually.
Record which service, revision and test caused the rollback.
