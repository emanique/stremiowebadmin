# StremioServerWebAdmin v0.1.4 — Source preference, IPTV and offline subtitles

## Scope

Release v0.1.4 consolidates Stremio Core, WebAdmin, IPTV, Subtitle Translator,
shared My Library source preferences and the WebPlayer's Continue Watching
preferred-source navigation. The distribution is **source-built**; it does
not contain individual torrents, playlists, credentials or subtitle models.

## Improvements

- IPTV management via WebAdmin: URL/file M3U, refresh, per-channel visibility,
  search, ordering and on/off without restarting the Core. HLS output is
  managed server-side; browser and native-player codec compatibility remains
  source-dependent.
- Subtitle Translator: CPU-first offline CTranslate2 inference, dynamic
  discovery/enablement of available source languages, explicit user selection
  of embedded/local/provider/captured subtitles and independent persistent
  cache per source variant. Original subtitle downloads are not modified.
- Translated SRT begins with bilingual attribution and language-of-origin
  metadata, without altering dialogue timestamps. Existing translations are
  upgraded when read, without retranslation.
- WebPlayer preferred play: authenticated same-account Continue Watching may
  bypass the stream list if one verified cached single-file source matches
  the title and the server's last source preference. Otherwise native stream
  selection remains available. This client-side optimization does **not**
  assert control of native Stremio App navigation.
- Shared My Library source preference, retained server-side with the verified
  infohash and file index. Multi-episode packs are excluded from automatic
  preferred play until episode-level identity is proven.
- Separate optional model installers: SHA-256 verified minimal language set;
  further languages sourced from compatible Argos catalogue packages.
- Security: no account tokens, provider URLs, certificates, translated SRTs,
  downloaded content or model binaries included in the release.

## Install / upgrade

See README.md for fresh-install, model provisioning, ports and administration.
Use `stremio-webadmin_0.1.4_all.deb` followed by `stremio-webadmin-setup`, or
build from the checked-out source via Docker Compose and the host setup tool.
Preserve persistent volumes and `.env` during upgrades. Never use
`docker compose down -v` on a server holding user data.

## Acceptance limitations

QA source tests and image build are necessary but do not replace manual
WebPlayer and native App smoke tests, including cross-device same-account
resume, live IPTV on different playback engines, translated SRT playback,
and clean-host installation. A failed or unavailable translation model is
not advertised in WebAdmin. The first non-cached translation can exceed a
native client's timeout; reselect the cached variant after it finishes.
