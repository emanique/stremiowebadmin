<div align="center">

# Stremio WebAdmin — Standalone

### Self-hosted Stremio Core, WebAdmin and WebPlayer with hardware-aware streaming

![Linux](https://img.shields.io/badge/platform-Debian%20%7C%20Ubuntu-2563eb?logo=linux&logoColor=white)
![Docker](https://img.shields.io/badge/runtime-Docker%20Compose-2496ED?logo=docker&logoColor=white)
![GPU](https://img.shields.io/badge/transcoding-VAAPI%20%7C%20CPU-7C3AED)
![Core](https://img.shields.io/badge/Stremio_Core-1.6.34-5B21B6)
![Status](https://img.shields.io/badge/status-first%20public%20release%20in%20preparation-F59E0B)

**Run a Stremio/libtorrent streaming backend with a separate browser-based administration interface, WebPlayer, adaptive playback, and host-level network management.**

[Features](#-features) · [Gallery](#-webadmin-gallery) · [Architecture](#-architecture) · [Installation](#-installation) · [Security](#-security) · [Release readiness](#-release-readiness)

</div>

---

> **Release status:** the public repository is the installable distribution of the private development project. The first standalone release is being prepared; the documented clone installation depends on Docker and host prerequisites. No GitHub Actions run in this public repository. Production-ready packages must only be attached after local validation, version parity and checksum verification.

## ✨ Features

| | Component | Capability |
|---|---|---|
| 🖥️ | **Independent WebAdmin** | Browser-based administration, runtime health, configuration, logs and diagnostics, separate from the streaming server. |
| 🎬 | **Stremio Core** | Self-hosted libtorrent server exposing the Stremio API, media streams and HTTPS endpoints. |
| ▶️ | **WebPlayer** | Direct stream where supported; HLS conversion where required for browser compatibility. |
| ⚡ | **Hardware acceleration** | Intel/DRM VAAPI detection with safe software fallback and preserved device selection across server recreation. |
| 🎞️ | **HLS and IPTV** | On-demand HLS, live MPEG-TS remultiplexing for supported H.264/AAC sources, bounded sliding playlists and FFmpeg cleanup. |
| 📝 | **Playback continuity** | Resume position, duration and Continue Watching integration with the Stremio runtime. |
| 🔌 | **BitTorrent ports** | Consistent host `STREMIOSRV_BT_LISTEN_PORT` configuration and TCP/UDP publishing. |
| 🔐 | **Optional external VPN Gateway** | Read-only, authenticated provider port observation; the standalone Stremio reconciler owns any `.env` update and server recreation. |
| 🧩 | **Host reconciliation** | Controlled network, DNS, GPU, service and port configuration through a systemd reconciliation timer. |
| 📦 | **Debian packaging** | Installable `.deb` plus source archive and SHA-256 verification, subject to public release QA. |

## 🖥️ WebAdmin gallery

The initial screenshot set has been captured from the running standalone application. The files are being reviewed and prepared for publication. The gallery below identifies each capture; image elements will be activated only after the files are present in both repositories, to avoid broken README images.

| Screenshot | Functional evidence |
|---|---|
| `docs/screenshots/dashboard.png` | Service status, HTTPS certificate, runtime versions, streams/cache and automatic transcoding status |
| `docs/screenshots/configuration.png` | Server parameters: network, BitTorrent, caching, bandwidth and seeding |
| `docs/screenshots/configuration-2.png` | Playback, adaptive download, next-episode prefetch and automatic transcoding |
| `docs/screenshots/configuration-3.png` | Library access, TLS and diagnostics |
| `docs/screenshots/logs.png` | Separate Stremio and WebAdmin logs and troubleshooting |
| `docs/screenshots/port-forwarding.png` | Host-owned TCP/UDP BitTorrent port; Gateway verification status is explicitly distinguished |
| `docs/screenshots/library.png` | My Library addon, saved/downloaded media and cache management |
| `docs/screenshots/vaapi.png` | VAAPI device readiness, codec capability and live FFmpeg metrics |
| `docs/screenshots/playback.png` | Streams, cache utilisation, download progress and Pin/Delete controls |
| `docs/screenshots/webplayer.png` | IPTV channel discovery and the web-based streaming interface |

The screenshots demonstrate the interface and observed functionality, **not** successful external NAT-PMP assignment, Internet peer reachability or successful playback on every television platform. The media view and TLS/port indicators are runtime snapshots rather than blanket compatibility guarantees.

<!-- Once reviewed images are uploaded to BOTH repos in docs/screenshots/,
replace this table with a responsive two-column image gallery.
Do not disclose DNS/IP identifiers, QR connection data, addon tokens or
private media history in the public screenshots. -->

## 🚀 What makes the standalone edition different?

This project is intentionally **not** the complete Gluetun + Pi-hole distribution of [Stremio Server WebAdmin](https://github.com/emmanique/stremio-webadmin).

Its focus is a smaller, independently administered Stremio runtime. The external VPN Gateway remains responsible for VPN routing, kill switch, DNS policy, NAT-PMP lease and provider port discovery. Stremio remains responsible for its own media processes, BitTorrent configuration and Docker container lifecycle.

### Streaming and transcoding

The runtime determines when media can be streamed directly and when remultiplexing or transcoding is needed. Hardware acceleration is selected only when a supported device is available. HLS workload reuse and process cleanup limit duplicate FFmpeg sessions.

For supported live MPEG-TS IPTV inputs, the HLS pipeline can copy H.264/AAC, convert AAC ADTS headers where required and keep a bounded playlist. Browser and device compatibility still depends on the source codecs and client capabilities.

### Gateway port synchronisation

The intended integration is:

```text
VPN Gateway → authenticated port observation (TCP/UDP)
            → bootstrap-installed host observer
            → root-owned observation file
            → Stremio reconciler
            → .env (STREMIOSRV_BT_LISTEN_PORT)
            → controlled Stremio container recreation only on change
```

A valid provider mapping is **not** proof of inbound Internet peer reachability. A missing, stale or invalid observation must not cause a guessed port update. Gateway credentials never belong in the repository or public bootstrap.

## 🏗️ Architecture

```text
                   Browser / Stremio App
                          |
              +-----------+-----------+
              |                       |
        HTTPS WebAdmin          WebPlayer / API
          (nginx 443)          (8080 / 11470)
              |                       |
    Host reconciliation      Stremio/libtorrent
    + status diagnostics     HTTPS / HLS / media
              |                       |
              +------- Docker -------+
                          |
                Host DNS / networking
                          |
             Optional external VPN Gateway
              (separately installed)
```

The WebAdmin and Stremio server have distinct lifecycles; management should remain accessible while the streaming container is restarted. The architecture does not require a bundled VPN gateway or Pi-hole container.

## 📦 Installation

### Install from the public Git repository

Once a source snapshot has been promoted and validated, users can clone the public repository and run its standalone setup on a Debian/Ubuntu host with Docker Compose, nginx, Python 3, OpenSSL and network utilities installed:

```bash
sudo git clone https://github.com/emanique/stremiowebadmin.git /opt/stremio-server-webadmin
sudo sh /opt/stremio-server-webadmin/packaging/stremio-webadmin-setup.sh
```

**The full standalone source has now been promoted to the public `main` branch and is available for cloning.** The setup builds Docker images locally and configures host services. A clean installation on an independent QA machine is still required before declaring the first tagged release production-ready.

### Install from a release package

The Debian/Ubuntu installation command after the first verified release is:

```bash
sudo apt install ./stremio-webadmin_<release>_all.deb
sudo stremio-webadmin-setup
```

The Debian package carries audited source and builds the required Docker images locally. **It does not yet provide immutable, published GHCR images.** The setup must preserve the existing host `.env` and persistent volumes; do not use `docker compose down -v` during a normal upgrade.

### Default endpoints

| Service | Address |
|---|---|
| HTTPS WebAdmin | `https://HOST_IP/` |
| WebPlayer | `http://HOST_IP:8080/` |
| Stremio API | `http://HOST_IP:11470/` |
| Stremio HTTPS | `https://HOST_IP:12470/` |
| BitTorrent | The effective TCP/UDP port in `.env`, verified against Docker and `/netcheck.json` |

These are defaults, not a guarantee that a firewall or reverse proxy permits external access.

## 🔒 Security

**The current standalone WebAdmin has privileged Docker management capabilities and is not suitable for direct Internet exposure without an independently validated authentication boundary.** Restrict TCP 443 to a trusted administrator LAN and apply host firewall rules. Treat the Docker socket, environment files, provider credentials and API tokens as privileged secrets.

For a remote VPN Gateway, use TLS trust validation, a dedicated read-only token, the expected host MAC/IP and policy identity, a current lease, and verified matching TCP/UDP port values. Never embed private keys or tokens in examples, screenshots or release packages.

## 🧪 Release readiness

**Current public source candidate:** standalone `0.1.2` (WebAdmin `3.0.14`, Core `1.6.34`). Public clone and source/DEB archive builds have been validated. Python 3.12 compilation, Docker Compose base/VAAPI configuration and package SHA-256 validation passed. The initial isolated regression executed 1,506 passing tests, 59 skips and 14 environment-sensitive failures; storage-dependent tests were subsequently rerun with 31 passing and 3 external-metadata skips, Docker Compose contracts passed (4/4) and the Library inline JavaScript passed Node syntax checking. This is a set of targeted validations, **not** a passed clean-install/end-to-end release gate.

A public release will require a clean installation on a separate QA host, reproducible source/package creation, unit and functional tests, Docker health checks, playback regression, validation of the authentication and network boundary, and SHA-256 checks of every downloadable package.

The private development repository is [emanique/StremioServerWebAdmin](https://github.com/emanique/StremioServerWebAdmin) and is the sole source of truth. The public repository is a read-only distribution model for consumers (clone, setup and tagged packages), with **no GitHub Actions**. Development, tests, release candidates and quality gates run privately or locally. Promotion copies only approved source, documentation and install assets from an exact Git commit; the README and release version must match, and .env, credentials, keys, caches, test outputs and internal tooling must be excluded. See [Release and promotion process](docs/RELEASE_PROCESS.md).

The initial source audit also identifies open acceptance items: extended IPTV playback in a real client, second-host clean installation, real authenticated Gateway port changes, and immutable GHCR image distribution. These are **not** claimed as completed.

## 📸 Screenshot contribution

The gallery filenames above are stable in **both** repositories. Add screenshots to `docs/screenshots/` in the private DEV repository first, with sensitive information removed; the approved set is promoted unchanged to the public repo together with the corresponding README. Do not use screenshot links before the image files exist.

## 📄 Provenance and licensing

This is a standalone derivative distribution. It tracks Stremio Core and a WebAdmin platform as separately versioned components. The original reference project is [emmanique/stremio-webadmin](https://github.com/emmanique/stremio-webadmin); code provenance and the applicable licence must remain intact in the first source release.

---

<sub>Maintained for self-hosted deployment. Screenshots and detailed installation evidence will be added once the first public release candidate has completed QA.</sub>
