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

> **Publication status:** this repository is being prepared for its first standalone release. Until a source package, Debian package, SHA-256 manifest and release notes are attached to a verified GitHub Release, the commands below describe the intended distribution, not a published installer.

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

Screenshots are being prepared from the functional application. The slots below describe the required captures; images will only be displayed after they have been supplied and reviewed.

| Screenshot to provide | What it should show |
|---|---|
| `docs/screenshots/dashboard.png` | Dashboard, health and component versions |
| `docs/screenshots/configuration.png` | General server and network configuration |
| `docs/screenshots/playback.png` | Streaming activity, peers and transcoding |
| `docs/screenshots/vaapi.png` | GPU/VAAPI device detection and codec capability |
| `docs/screenshots/library.png` | Media/library management, where enabled |
| `docs/screenshots/port-forwarding.png` | Local BitTorrent port and authenticated Gateway assignment |
| `docs/screenshots/logs.png` | Service logs and diagnosis |
| `docs/screenshots/webplayer.png` | WebPlayer with live playback |

<!-- When screenshots are available, replace this comment with a 2-column HTML
gallery referencing the filenames above. Check that private IPs, API keys,
tokens, public IPs and identifiable media account data have been redacted. -->

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

The planned Debian/Ubuntu installation for the first verified public release is:

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

A public release will require a clean installation on a separate QA host, reproducible source/package creation, unit and functional tests, Docker health checks, playback regression, validation of the authentication and network boundary, and SHA-256 checks of every downloadable package.

The initial source audit also identifies open acceptance items: extended IPTV playback in a real client, second-host clean installation, real authenticated Gateway port changes, and immutable GHCR image distribution. These are **not** claimed as completed.

## 📄 Provenance and licensing

This is a standalone derivative distribution. It tracks Stremio Core and a WebAdmin platform as separately versioned components. The original reference project is [emmanique/stremio-webadmin](https://github.com/emmanique/stremio-webadmin); code provenance and the applicable licence must remain intact in the first source release.

---

<sub>Maintained for self-hosted deployment. Screenshots and detailed installation evidence will be added once the first public release candidate has completed QA.</sub>
