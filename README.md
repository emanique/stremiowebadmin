<div align="center">

# Stremio WebAdmin — Standalone

## Versão estável v0.1.3

**Release estável:** [v0.1.3](https://github.com/emanique/stremiowebadmin/releases/tag/v0.1.3). Consolidada a partir da RC9 depois de validações LAN, HTTPS/WebAdmin sem Basic Auth, VAAPI, reprodução/transcoding/retoma, observação NAT-PMP do VPN Gateway v1.0.8, reversão da porta para 6881 e recuperação após reboot dos dois servidores. O Stremio Core mantém a sua versão independente (1.6.34).

**Instalação limpa:** o WebAdmin não exige login na LAN privada identificada (CIDR /24 ou mais restrito); não exponha directamente esta interface à WAN. A atribuição da porta VPN é uma funcionalidade opcional e automática apenas quando há política de host autorizada no Gateway e rede de gestão protegida. A admissão inicial TLS utiliza TOFU e não substitui uma PKI em LAN não fiável. A versão do ProxyWAN é independente.


#
### Acesso WebAdmin sem password numa LAN de QA

A instalação pública configura **acesso HTTPS sem password por defeito**, limitado automaticamente à sub-rede IPv4 privada efectivamente configurada na interface de gestão (prefixo /24 ou mais restrito). O WebAdmin possui privilégios de gestão do Docker: não exponha a porta TCP/443 a redes não confiáveis. Para uma instalação anterior, pode optar explicitamente por uma política LAN através de:

```bash
sudo sh -c 'printf "lan:192.168.1.0/24\\n" > /etc/stremio-webadmin/access-policy'
sudo chmod 600 /etc/stremio-webadmin/access-policy
sudo python3 /opt/stremio-server-webadmin/scripts/configure-host-https.py --apply
```

Substitua `192.168.1.0/24` pela rede LAN real (mínimo /24 e sempre privada). O IP do próprio Stremio tem de pertencer a essa rede. A política persiste quando o configurador voltar a ser executado. Para restaurar autenticação, escreva `basic` no mesmo ficheiro e volte a executar `--apply`. Não utilize esta opção se a interface for acessível por utilizadores não confiáveis ou publicada na WAN. O comportamento automático sem autenticação só se aplica ao código corrigido posteriormente à tag RC8. A RC8 anteriormente publicada ainda gera credenciais; a RC9 já aplica automaticamente esta política.


## Self-hosted Stremio Core, WebAdmin and WebPlayer with hardware-aware streaming

![Linux](https://img.shields.io/badge/platform-Debian%20%7C%20Ubuntu-2563eb?logo=linux&logoColor=white)
![Docker](https://img.shields.io/badge/runtime-Docker%20Compose-2496ED?logo=docker&logoColor=white)
![GPU](https://img.shields.io/badge/transcoding-VAAPI%20%7C%20CPU-7C3AED)
![Core](https://img.shields.io/badge/Stremio_Core-1.6.34-5B21B6)
![Status](https://img.shields.io/badge/status-public%20pre--release-F59E0B)

**Run a Stremio/libtorrent streaming backend with a separate browser-based administration interface, WebPlayer, adaptive playback, and host-level network management.**

[Features](#-features) · [Gallery](#-webadmin-gallery) · [Architecture](#-architecture) · [Installation](#-installation) · [Security](#-security) · [Release readiness](#-release-readiness)

</div>

---

> **Release status:** the public repository is the installable distribution of the private development project. The current standalone RC8 candidate is available; the documented clone installation depends on Docker and host prerequisites. Release automation verifies and publishes packages using GitHub Actions. Production-ready packages must only be attached after local validation, version parity and checksum verification.

**Release RC9:** [v0.1.3-rc.9](https://github.com/emanique/stremiowebadmin/releases/tag/v0.1.3-rc.9). A instalação nova não exige login no WebAdmin quando a interface de gestão está numa LAN privada /24 ou mais restrita. A sincronização do BitTorrent consulta automaticamente a autorização do host no VPN Gateway `v1.0.8` quando este é o gateway predefinido: se IP/MAC, política, TCP/UDP e atribuição NAT-PMP estiverem válidos, actualiza `.env` e recria apenas o Core, verificando health e dispositivo VAAPI. Sem autorização válida, regressa à porta `6881` após uma tolerância de até 120 segundos para renovações de lease. O Gateway gere exclusivamente a política e o lease; o host Stremio é o único a escrever a porta.

**Confiança e limites:** a descoberta automática de primeiro contacto usa a identidade do próximo salto predefinido e *TOFU* (certificado TLS fixado localmente após verificar o IP no SAN); requer uma LAN privada fiável com protecção contra spoofing L2. Em redes não confiáveis, use a integração anterior com certificado de CA aprovado e token dedicado, não este modo automático. O Stremio não encaminha por VPN se o Gateway deixar de ter uma política que o autorize; a porta 6881 local não é prova de disponibilidade pública.

**Release RC8:** [v0.1.3-rc.8 e packages](https://github.com/emanique/stremiowebadmin/releases/tag/v0.1.3-rc.8). Contém registo HLS para entradas IPTV/HTTP sem infohash, associação dos processos FFmpeg às sessões e limpeza do registo após GC. Mantém a correcção do monitor de processos não registados da RC7. A promoção é candidata e não certificação de produção; testes prolongados de dispositivos IPTV, renovações de porta e avaliação completa de instalação limpa continuam distintos dos testes dirigidos executados.

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

The initial screenshot set has been captured and locally sanitised. Files are pending binary promotion to both repositories; the gallery intentionally uses a descriptive table until the images exist in Git. The gallery below identifies each capture; image elements will be activated only after the files are present in both repositories, to avoid broken README images.

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

Once a source snapshot has been promoted and validated, users can clone the public repository and run its standalone setup on a Debian/Ubuntu host with systemd. Missing Docker, Compose, Nginx, Python, OpenSSL and network utilities are installed by APT:

```bash
sudo git clone --branch v0.1.3 --depth 1 https://github.com/emanique/stremiowebadmin.git /opt/stremio-server-webadmin
sudo sh /opt/stremio-server-webadmin/packaging/stremio-webadmin-setup.sh
```

For independent QA, the pinned pre-release tag above is intentional; cloning the mutable main branch would not reproduce the same release. The source install still builds the Core and WebAdmin images locally; no pre-existing images should be assumed.\n\nOn a fresh Debian/Ubuntu host, the setup first installs missing Docker Engine,
Docker Compose v2, Docker Buildx, Nginx, Python, OpenSSL and iproute2 packages via APT.
It checks for existing packages before installing anything and aborts on
unsupported distributions. To require manual provisioning instead, set
`STREMIO_SKIP_DEP_INSTALL=1` in the setup environment.

**The full standalone source has now been promoted to the public `main` branch and is available for cloning.** The setup builds Docker images locally and configures host services. The corrected installer defaults to passwordless HTTPS restricted to the detected trusted private LAN; explicit `basic` access-policy remains supported if authentication is required. A separate Debian 13 KVM clean installation and reboot test passed. IPTV/device playback, external Gateway lease-change and security acceptance are still required before a stable release.

### Update an existing tag-based source installation

A source clone using `--branch v0.1.3-rc.8` is in detached-HEAD state. Do not use `git pull` directly. Preserve your existing `.env`, persistent volumes and any local changes before switching to the new public tag:

```bash
cd /opt/stremio-server-webadmin
sudo git fetch origin --tags
sudo git checkout v0.1.3
sudo sh packaging/stremio-webadmin-setup.sh
```

If the checkout reports modified files, **back them up first** (for example `git stash push -u`), then change tag; do not discard local settings or run `docker compose down -v`. The installer does not replace an existing `.env`. For a fresh clone, use the stable v0.1.3 clone command above.

The automatic trusted-LAN Gateway observer uses the active IPv4 default next hop. A matching, authorised policy is required in VPN Gateway Multi v1.0.8; if missing or revoked the Stremio BT listen port safely converges to 6881 after a 120-second lease-renewal grace, without changing Gateway routing policies. The first TLS certificate is pinned (TOFU); if the Gateway certificate later changes, the host refuses new observations until trust is explicitly re-established.

### Install from a release package

The Debian/Ubuntu installation command after the first verified release is:

```bash
sudo apt install ./stremio-webadmin_0.1.3_all.deb
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

**The standalone WebAdmin has privileged Docker management capabilities. Corrected new installs use HTTPS without a login only for the detected trusted LAN, enforced by Nginx `allow`/`deny`; the direct WebAdmin remains on loopback.** Restrict TCP 443 to a trusted administrator LAN and apply host firewall rules. Treat the Docker socket, environment files, provider credentials and API tokens as privileged secrets.

For a remote VPN Gateway, use TLS trust validation, a dedicated read-only token, the expected host MAC/IP and policy identity, a current lease, and verified matching TCP/UDP port values. Never embed private keys or tokens in examples, screenshots or release packages.

## 🧪 Release readiness

**Historical pre-release candidate (RC8):** standalone `0.1.3-rc.8` (WebAdmin `3.0.14`, Core `1.6.34`). **Historical RC6 QA evidence (not a new RC8 full-suite claim):** The frozen Python 3.12 regression passed on Debian 13 QA: 1,629 passed, 2 skipped, 0 failed** using Python 3.12, Docker Compose, Node.js and a real-disk TMPDIR. Base and VAAPI Compose configuration, Python compilation and Debian/source package SHA-256 verification also passed. A separate Debian 13 KVM clean-install QA passed, including both containers healthy, HTTP endpoints, HTTPS Basic authentication and recovery after an OS reboot. The reconciler now consumes the separate root-owned Gateway observer record and simulated port change/rollback checks passed. Live authenticated Gateway port renewal, IPTV/device playback and external NAT reachability are not yet accepted; no production readiness is implied.

A public release will require a clean installation on a separate QA host, reproducible source/package creation, unit and functional tests, Docker health checks, playback regression, validation of the authentication and network boundary, and SHA-256 checks of every downloadable package.

The private development repository is [emanique/StremioServerWebAdmin](https://github.com/emanique/StremioServerWebAdmin) and is the sole source of truth. The public repository is a read-only distribution model for consumers (clone, setup and tagged packages), with a release-only GitHub Actions workflow. Development, tests, release candidates and quality gates run privately or locally. Promotion copies only approved source, documentation and install assets from an exact Git commit; the README and release version must match, and .env, credentials, keys, caches, test outputs and internal tooling must be excluded. See [Release and promotion process](docs/RELEASE_PROCESS.md).

The initial source audit also identifies open acceptance items: extended IPTV playback in a real client, second-host clean installation, real authenticated Gateway port changes, and immutable GHCR image distribution. These are **not** claimed as completed.

## 📸 Screenshot contribution

The gallery filenames above are stable in **both** repositories. Add screenshots to `docs/screenshots/` in the private DEV repository first, with sensitive information removed; the approved set is promoted unchanged to the public repo together with the corresponding README. Do not use screenshot links before the image files exist.

## 📄 Provenance and licensing

This is a standalone derivative distribution. It tracks Stremio Core and a WebAdmin platform as separately versioned components. The original reference project is [emmanique/stremio-webadmin](https://github.com/emmanique/stremio-webadmin); code provenance and the applicable licence must remain intact in the first source release.

---

<sub>Maintained for self-hosted deployment. Screenshots and detailed installation evidence will be added once the first public release candidate has completed QA.</sub>