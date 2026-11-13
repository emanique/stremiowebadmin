# StremioServerWebAdmin

Standalone Docker Compose Stremio Core 1.6.34 + WebAdmin 3.0.14 + WebPlayer, IPTV Addon and offline Subtitle Translator.
The product release version is tracked separately in `RELEASE_VERSION`.

## Installation (Debian / Ubuntu)

```sh
sudo apt install ./stremio-webadmin_0.1.4_all.deb
sudo stremio-webadmin-setup
```

The package ships audited source; the installer builds Docker images locally.
It is **not** an immutable GHCR deployment. It preserves an existing host
`.env` and persistent Docker volumes and refuses a conflicting active
IP/BitTorrent port instead of silently resetting them. Never use
`docker compose down -v` during a routine upgrade.

For a **fresh source installation** using the public repository (no access to
the private development repository is needed), start with a clean Debian or
Ubuntu host with `git` installed. The default host installation root is
`/opt/stremio-server-webadmin`, so clone directly into that directory:

```sh
sudo apt-get update
sudo apt-get install -y git
sudo git clone --branch main --single-branch \
  https://github.com/emanique/stremiowebadmin.git \
  /opt/stremio-server-webadmin
sudo sh /opt/stremio-server-webadmin/packaging/stremio-webadmin-setup.sh
```

The setup script installs or checks Debian/Ubuntu prerequisites, creates a
protected `.env` only when absent, configures host HTTPS, validates settings,
starts the Compose deployment and enables the host reconciliation timer.
**Do not run this clone command over an existing installation**. For upgrades,
follow `docs/UPGRADE_GUIDE.md` and preserve the existing `.env` and volumes.
To use a different destination, set `STREMIO_INSTALL_ROOT` consistently for
the setup script and its companion services.

## Network and administration

- WebAdmin: `https://SERVER_HOST/` (Nginx 443, local WebAdmin 8090).
- WebPlayer: `http://SERVER_HOST:8080/`.
- Core: `http://SERVER_HOST:11470/` and `https://SERVER_HOST:12470/`.
- Effective BitTorrent port: check Core `/netcheck.json`; this is host-owned
  and not necessarily the default 6881.
- Host IPv4, DNS, GPU and port settings are reconciled by the systemd
  `stremio-webadmin-reconcile.timer`, without changing the VPN Gateway.
- WebAdmin controls Docker and must be protected. By default, host Nginx allows passwordless management only from a detected trusted private LAN. An optional Basic Auth policy supports password-protected administration; see scripts/configure-host-https.py and scripts/change-webadmin-password.py. Do not expose WebAdmin management directly to the public Internet.

The optional host-only Gateway port synchronizer needs a dedicated read-only
token, authenticated policy, matching host MAC/IP and trusted TLS CA. Without
these, the interface reports the local effective port but **does not claim
that NAT-PMP is assigned or that WAN connectivity works**.

## Playback

The Core owns direct/transcode decisions. The App uses HTTP Range for supported
direct streams; the WebPlayer uses HLS when necessary. Continue Watching,
source duration and HLS seek are covered by regression tests.

External IPTV M3U entries may point to continuous MPEG-TS, not HLS manifests.
For detected live H.264/AAC sources, HLS/fMP4 remultiplexing copies codecs,
converts AAC ADTS headers to ASC, and maintains a bounded eight-segment
sliding playlist. The AAC filter does not affect VOD copying. Repeated HLS
requests reuse a workload; failed FFmpeg restarts have a bounded retry budget.
The HLS playback registry also tracks external, non-torrent sessions without
inventing torrent identifiers, and garbage-collection reaping closes stale
HLS sessions. Subtitle FFmpeg failures are classified without logging source
URLs or private playlist credentials.

## Guided upgrade of an existing server

The migration is **not** a replacement of persistent cache/configuration and
does not import provider credentials into source control. On the original
server, preserve the existing Compose volumes, `.env`, TLS, GPU settings and
BT port before applying new images. See [controlled existing-server upgrade
procedure](docs/UPGRADE_GUIDE.md).

Versioned tooling:

- `python3 scripts/preflight-upgrade.py` inventories the live host without
  exporting secrets.
- `python3 scripts/snapshot-before-upgrade.py` backs up stopped containers'
  persistent volumes and configuration into a local restricted folder.
- `bash scripts/provision-subtitle-models.sh --install` installs only
  SHA-256-verified English, French-pivot and Spanish CPU models.

Core and WebAdmin are available on the normal host ports, including
`https://SERVER_HOST/`; Stremio addon manifests use trusted streaming HTTPS
on 12470, configured separately with
`STREMIOSRV_IPTV_PUBLIC_ORIGIN` and
`STREMIOSRV_SUBTITLE_PUBLIC_ORIGIN`. No live-server upgrade is implied by the
source commit: perform a guarded cutover only after a restorable snapshot.

## Release QA

```sh
uv sync --frozen --group dev
.venv/bin/pytest -q --disable-warnings
bash scripts/build-qa-release.sh dist
(cd dist && sha256sum -c SHA256SUMS)
```

Review `docs/SOURCE_AUDIT_2026-11-06.md` for feature coverage and open
operational acceptance items. In particular: browser IPTV endurance,
second-host clean install, external authenticated Gateway lease changes, and
immutable GHCR images have **not** been established by source tests.

Source provenance: `SOURCE_PROVENANCE.txt`. Licence: `LICENSE`.

## IPTV Addon (local M3U/M3U8)

- The WebAdmin **IPTV** tab accepts an M3U/M3U8 URL or playlist file, tests
  reachability, reports channel counts, supports refresh without restarting
  Core, searchable channel listings, channel ordering, per-channel removal,
  and addon enable/disable. The server reports refresh status and errors.
- The addon supplies a Stremio manifest and exposes channels to the native
  App and WebPlayer. Imported playlists and provider credentials belong to
  the persistent `/config` volume, **never** the Git repository.
- IPTV output format is chosen by the Core: a TS/live channel is not assumed
  to be a browser-compatible HLS stream. H.264/AAC packaging and bounded
  transcoding/re-muxing support are implemented, but compatibility still
  depends on the source and the playback device (ExoPlayer, MPV, Samsung TV).
- One client selecting another channel must not leave unlimited orphaned
  FFmpeg sessions. Check the WebAdmin active-playback view and server logs.

## Subtitle Translator Addon (offline, pt-PT)

- A separate **Subtitle Translator** tab at `https://SERVER_HOST/#subtitleTranslator`
  displays installed translation models, lets operators enable source
  languages and set lookup priority, and persists preferences without a Core
  restart. The default target is **Português (Portugal)**.
- Translation happens **on demand**, after the user selects a subtitle variant;
  it is not speech translation, live AI captioning, or automatic interception
  of every subtitle chosen from a different Stremio addon.
- Sources include subtitles discovered through configured addons, embedded
  tracks in **complete** media, safe local subtitle files, and SRT/VTT content
  already fetched through the Core's subtitle proxy during playback. The last
  mechanism requires an unambiguous active film and a confidently detected
  source language; if the App fetches directly from an outside provider it
  may bypass capture. Partial downloads do not grant unsafe extraction of
  embedded subtitle tracks.
- Multiple selected releases are preserved independently. Their source
  provider, language, and short source identifier distinguish them; choosing
  a different release never replaces an existing translated SRT. Repeated
  identical selections are deduplicated. The menu exposes cached translations
  when the film has been resolved to the same verified torrent `infohash`.
- Translated `.srt` files live under the movie's persistent download-cache
  directory and are reused without new inference; they are not in the
  transcoding cache. Cached SRT content is removed with the movie's cache.
- Each eligible SRT starts with the bilingual attribution, ending **one
  second before** the first original cue; it also identifies the detected
  source language (for example `Inglês (EN) → Português (pt-PT)`). Existing
  translated cues retain their timings and the notice is idempotent. If the
  first original cue starts at or before one second, the notice is omitted
  rather than overlapping dialogue.
- The translated subtitle is delivered as an ordinary SRT over the configured
  trusted HTTPS streaming origin. First use may take substantially longer
  than a cache hit. A client-side timeout is **not** proof that translation
  failed; reselect the cached variant after processing completes.

### Offline language model provisioning

#### Requirements, paths and security

1. Install the Core/WebAdmin from a release or checked-out source, and ensure
   the Compose project can build its Core image. The Core image includes
   `ctranslate2==4.9.0`, `sentencepiece==0.2.3` and `langdetect==1.0.9`;
   **Argos model files are separate** and are never installed on demand by
   playback. The host requires `bash`, `python3`, `curl`, and access to the
   upstream model endpoints for online provisioning.
2. Reserve sufficient persistent storage: approximately **3 GiB or more**
   for the tested extended collection, with additional free space for
   temporary archives. The base set is considerably smaller. Available
   languages and model sizes depend on upstream packages.
3. Set the host directory through `STREMIOSRV_SUBTITLE_MODELS_DIR` in `.env`
   (Compose variable); prefer an **absolute path on the persistent host
   volume**, for example `/srv/stremio/subtitle-models` (a generic example,
   not an address or a requirement). The Core sees that directory read-only
   at `/opt/stremio-subtitle-models`, configured by
   `STREMIOSRV_SUBTITLE_MODELS_ROOT`. Do not use the Docker image filesystem
   or transcoding cache as the persistent model store.
4. Confirm that the host path is writable by the operator performing the
   installation and readable by the Docker Core. Never place provider API
   keys, playlist URLs, user subtitles or account tokens alongside the
   publicly distributed model-installer scripts.

#### Minimal offline translation (English, Spanish and French)

From the repository root, after setting the models directory in `.env`:

```sh
# Use the SAME directory configured in the deployment .env.
export STREMIOSRV_SUBTITLE_MODELS_DIR="/srv/stremio/subtitle-models"
mkdir -p "$STREMIOSRV_SUBTITLE_MODELS_DIR"
bash scripts/provision-subtitle-models.sh --download-and-install
bash scripts/provision-subtitle-models.sh --verify
```

The base installer uses **pinned SHA-256 digests** and provisions verified
CTranslate2 packages for `en → pt`, `es → pt`, `fr → en` (French subsequently
pivots via `en → pt`). The installer checks the expected files:
`<root>/<source>_<target>/model/model.bin`, `model/config.json` and
`sentencepiece.model`. It does not embed binary models in Git or publish
user subtitles.

#### Optional extended installation (additional languages)

Once the minimal installation has passed verification, continue with the
**same absolute path** configured in `.env` and used by the baseline model
installer. The directory in the command below is illustrative and can be
replaced by any existing persistent host path.

```sh
export STREMIOSRV_SUBTITLE_MODELS_DIR="/srv/stremio/subtitle-models"
python3 scripts/provision-all-subtitle-models.py
python3 scripts/provision-all-subtitle-models.py --install \
  --root "$STREMIOSRV_SUBTITLE_MODELS_DIR" --workers 3
```

The first invocation **lists** upstream source→English candidates without
installing anything. The second downloads compatible Argos CTranslate2
packages into a temporary directory and installs each model pair into the
persistent directory. Existing model pairs are preserved. Only successfully
installed compatible models appear in the application: catalogue entries,
failed downloads and unsupported legacy packages are not counted as working
languages. A multi-step path (e.g. German→English→Portuguese) requires **both**
individual model pairs; the presence of German→English alone is insufficient.
An earlier validation installation included **28 compatible pairs covering
27 source languages** (the baseline three included):
`en, fr, es, bg, bn, de, el, et, eu, gl, ky, lv, ms, nb, nl, pb, pl, ru,
sk, sl, sq, sw, th, tl, vi, zh, zt`. These are an observed reference set,
**not** a guarantee that the current catalogue can reproduce every language.
The WebAdmin dynamically advertises only verified installed pairs with a
complete source → pt-PT translation path. This optional collection and its
approximate 2.5 GiB of binaries are not shipped in the Git repository or
standard release package.

The optional script fetches the current provider catalogue: it cannot promise
that every candidate remains available or compatible, and it records a SHA-256
for the downloaded package for local audit. For reproducible regulated/offline
installations, download and pre-approve model archives, verify trusted package
checksums, and stage them separately rather than relying on a mutable remote
catalogue. The optional installer records a download checksum after retrieval,
but **does not validate an externally pinned checksum before installation**;
only the baseline installer provides that stronger verification.

#### Validate and troubleshoot

After provisioning, check the model directories, Compose mount and API/UI:

```sh
find "$STREMIOSRV_SUBTITLE_MODELS_DIR" -mindepth 1 -maxdepth 1 -type d | sort
# From the Compose project directory:
sh start.sh config --quiet
# Start the service if not already active; models need no Core restart when
# the mount is already in place and accessible:
sh start.sh up -d --no-build
```

Open **WebAdmin → Subtitle Translator**. The displayed source languages are
**discovered from valid installed model paths**, not from the installer log.
Enable the desired languages and set priority; save without restarting Core.
Test a short English subtitle first, check the pt-PT SRT, then a pivot path
(e.g. French). Confirm the original SRT timings, initial bilingual notice,
source-language attribution, caching and playback in the native App and
WebPlayer separately.

If a language is absent, verify its required pair(s), the mounted model
path, file permissions, and readable non-empty `model.bin`, `config.json` and
`sentencepiece.model`. If the catalogue offers a legacy non-CTranslate2
package, it is **not** usable with this engine. Model inference is CPU-first;
GPU availability for video transcoding does not mean translation inference
uses the same GPU. A first translation can take considerably longer than
retrieving a cached SRT. Do not erase the download-cache volume to resolve a
model problem.


### Native App versus WebPlayer

Stremio native clients decide how language names and addon-specific subtitle
names are displayed. The addon supplies the full source/provider label, but
some clients display only `Português — Subtitle Translator`; the original
language is also written into the translated SRT's opening attribution.
The WebPlayer and native App should be tested **independently** for subtitle
menu visibility, asynchronous first translation, SRT loading, seeking and
transcoding. Do not infer WebPlayer acceptance from App acceptance.

## Preferred source, Continue Watching and multi-device behaviour

- The server maintains the verified mapping `Stremio title ID → torrent
  infohash → file index`, independently of the browser. On playback it
  records the last used source and advertises a **My Library** stream marked
  `Em cache no servidor · Última fonte utilizada`. The cached file must still
  exist and pass the complete-file check.
- The bundled WebPlayer has a `Continue watching` preferred-resume flow:
  when the same authenticated Stremio account opens a movie with a unique,
  verified cached single-file source, it can navigate directly to the Stremio
  player. If the preference is unavailable, incomplete or ambiguous, it
  falls back to Stremio's normal source selection. The user can still choose
  another source through the ordinary details/streams screen.
- Preference is **server-side**, so the same My Library addon installed on
  the same Stremio account and pointing to the same server can return the
  chosen source across devices. Native Stremio clients control their own
  navigation, and an addon cannot force them to skip the stream-selection
  page; direct autoplay is a **bundled WebPlayer-specific integration**.
  Likewise, playback resume position is Stremio account state, not a promise
  that every third-party native player honours an identical UI path.
- Multi-episode torrents need strict episode-to-file identity; this initial
  automatic preferred-play flow is deliberately restricted to validated
  single-file movies. Never infer file index solely from a matching title.

## Fresh installation and release acceptance

The public release and private `main` must contain the same audited source,
with `develop` used for QA. A fresh install requires a new machine or clean
VM, not a destructive reinstall of an existing validation host. Configure
HTTPS, trusted LAN access, explicit DNS and the effective BitTorrent port;
install optional offline translation models separately.

Validate before production adoption: installation and reboot persistence,
Stremio App and WebPlayer playback/seek/Continue Watching, same-account
My Library installation on a second device, one cached single-file movie,
three distinct subtitle selections and cached SRT retrieval, IPTV channels
in browser and native App, Intel VAAPI and available NVIDIA transcoding,
configuration persistence, rollback, and no leaked credentials in Git.
**Do not** invoke `docker compose down -v` for an upgrade: it removes user
media, downloads, translated subtitles and settings.
