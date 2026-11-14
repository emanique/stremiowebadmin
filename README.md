<div align="center">

# Stremio WebAdmin — Standalone

## Versão estável v0.1.4

**Release estável:** [v0.1.4](https://github.com/emanique/stremiowebadmin/releases/tag/v0.1.4). Distribuição independente do Stremio Core 1.6.34, WebAdmin 3.0.14 e WebPlayer, com gestão IPTV, tradução offline de legendas para pt-PT, cache persistente, identificação da língua de origem e preferência de fonte no servidor.

**Instalação limpa:** o WebAdmin usa HTTPS e permite acesso sem password apenas na LAN privada considerada fiável. Não exponha a gestão do Docker à Internet. A integração do VPN Gateway é opcional, exige autorização e validação de TLS; a porta de BitTorrent local não comprova, por si só, conectividade WAN.

**Validação da release:** 1.735 testes aprovados e 2 ignorados na suite de regressão alargada, excluindo um teste de pin de torrent dependente de condições externas; 22 testes focados passaram em `main`. Arquivo fonte, pacote Debian e checksums SHA-256 publicados. O fresh install funcional completo numa nova máquina continua a requerer aceitação operacional.



### Acesso WebAdmin sem password numa LAN de QA

O WebAdmin possui acesso privilegiado ao Docker. Por defeito, o Nginx disponibiliza HTTPS sem password **apenas numa rede privada de gestão confiável**, com política `allow/deny`; a porta interna de administração permanece no loopback. Pode optar por Basic Auth, se necessário.

Para aplicar uma política LAN explícita, substitua `LAN_CIDR` pelo prefixo privado da instalação; o exemplo não pressupõe um IP ou domínio fixo:

```bash
export LAN_CIDR="REDE_PRIVADA/PREFIXO"
printf 'lan:%s\n' "$LAN_CIDR" | sudo tee /etc/stremio-webadmin/access-policy >/dev/null
sudo chmod 600 /etc/stremio-webadmin/access-policy
sudo python3 /opt/stremio-server-webadmin/scripts/configure-host-https.py --apply
```

Para activar autenticação Basic, escreva `basic` no mesmo ficheiro e execute novamente o configurador. Os acessos WAN devem ser bloqueados por firewall ou reverse proxy de confiança; não utilize a política sem autenticação numa LAN partilhada com clientes não confiáveis.



## Self-hosted Stremio Core, WebAdmin and WebPlayer with hardware-aware streaming

![Linux](https://img.shields.io/badge/platform-Debian%20%7C%20Ubuntu-2563eb?logo=linux&logoColor=white)
![Docker](https://img.shields.io/badge/runtime-Docker%20Compose-2496ED?logo=docker&logoColor=white)
![GPU](https://img.shields.io/badge/transcoding-VAAPI%20%7C%20CPU-7C3AED)
![Core](https://img.shields.io/badge/Stremio_Core-1.6.34-5B21B6)
![Status](https://img.shields.io/badge/status-stable%20v0.1.4-16A34A)

**Run a Stremio/libtorrent streaming backend with a separate browser-based administration interface, WebPlayer, adaptive playback, and host-level network management.**

[Features](#-features) · [Gallery](#-webadmin-gallery) · [Architecture](#-architecture) · [Installation](#-installation) · [Security](#-security) · [Release readiness](#-release-readiness)

</div>

---

> **Release status:** versão pública estável [v0.1.4](https://github.com/emanique/stremiowebadmin/releases/tag/v0.1.4), com pacote `.deb`, arquivo fonte e `SHA256SUMS`. O pacote compila as imagens localmente; não depende de imagens GHCR imutáveis. A release não substitui a validação funcional num host limpo.

**Continuidade entre clientes:** o servidor guarda a fonte preferida por filme para o addon My Library; o **WebPlayer incluído** pode abrir directamente uma fonte local confirmada em «Continue Watching». A App Stremio nativa continua a controlar a sua própria navegação e pode apresentar a selecção de streams.

**Transcodificação e rede:** o Core gere vídeo, HLS e IPTV; o Gateway externo, quando autorizado, gere as políticas e o lease NAT-PMP. O host Stremio mantém a responsabilidade de actualizar a sua configuração BitTorrent, sem expor segredos no repositório.

---


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
| 📺 | **IPTV addon** | Import M3U/M3U8 por URL ou ficheiro, testar ligação, pesquisar/ordenar canais, actualizar sem reiniciar Core e gerir o addon. |
| 🌐 | **Subtitle Translator** | Tradução offline sob selecção para pt-PT, com modelos CTranslate2 opcionais e variantes independentes de legendas. |
| 💾 | **Subtitle cache** | Guardar SRT traduzido associado ao filme e à fonte escolhida; aviso bilingue indica a língua original. |
| 🔁 | **Preferred source** | Guardar a última fonte reproduzida no servidor, destacá-la em My Library e permitir Play directo no WebPlayer quando validada. |
| 📦 | **Debian packaging** | Installable `.deb` plus source archive and SHA-256 verification, with SHA-256 verified assets. |

## 🖥️ WebAdmin gallery

A galeria mantém os identificadores históricos das capturas do WebAdmin. Enquanto as imagens sanitizadas não estiverem publicadas no repositório, a tabela descreve os ecrãs sem criar ligações quebradas.

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
| `docs/screenshots/iptv.png` | Gestão IPTV: origem M3U/M3U8, teste de ligação, actualização, canais e estado |
| `docs/screenshots/subtitle-translator.png` | Idiomas activos, modelos offline, selecção de fontes e cache de tradução |
| `docs/screenshots/preferred-playback.png` | Fonte guardada no My Library e reprodução directa no Continue Watching |

A tabela documenta os ecrãs pretendidos; não certifica reprodução em todos os dispositivos, NAT-PMP externo ou funcionalidade que dependa de uma captura ainda não publicada. The media view and TLS/port indicators are runtime snapshots rather than blanket compatibility guarantees.

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
    + status diagnostics     HTTPS / HLS / media\n                                   + IPTV + subtitles
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

### Install from the public Git repository — fresh snapshot / QA candidate

Para uma máquina limpa (incluindo rollback do snapshot sem dados), usar o código
público `main` da candidata **v0.1.5-rc.5**. O instalador verifica ou instala
Docker Engine, Compose v2, Buildx, Nginx, Python, OpenSSL, curl e utilitários
de rede. Instala também **cinco modelos offline** verificados por SHA-256:
`en_pt`, `fr_en`, `es_pt`, `ru_en` e `cs_en` (BPE/Moses).

```bash
sudo apt-get update
sudo apt-get install -y git
sudo git clone --branch main --depth 1 \
  https://github.com/emanique/stremiowebadmin.git \
  /opt/stremio-server-webadmin
sudo sh /opt/stremio-server-webadmin/packaging/stremio-webadmin-setup.sh
```

Este procedimento só é para máquina sem instalação Stremio existente. O modelo
russo segue `ru → en → pt`; as legendas são traduzidas localmente. Os binários
Argos são obtidos de fontes externas com checksums fixos; o instalador interrompe
a operação caso falhe a verificação/download, sem ignorar a falha. Para uma
instalação intencionalmente sem modelos (por exemplo, sem Internet) utilizar
`sudo STREMIO_PROVISION_SUBTITLE_MODELS=0 sh /opt/stremio-server-webadmin/packaging/stremio-webadmin-setup.sh`
e executar o provisionamento mais tarde.

**Os modelos não são colocados no GitHub**. `.env`, certificados, caches,
playlists IPTV, tokens de VPN e definições locais também não são publicados.
A porta BT é inicialmente `6881` até ser associada a política/lease válido
do VPN Gateway. Acesso HTTPS ao WebAdmin está limitado à LAN administrativa.

A versão estável histórica continua disponível na tag `v0.1.4`, mas essa tag
**não contém** as últimas correcções por hash nem o bootstrap dos cinco
modelos. O `main` contém uma candidata de QA, não uma release estável.

**Não use estes comandos sobre um servidor com instalação existente**.
Para upgrades preserve a configuração, certificados e volumes Docker.



### Update an existing tag-based source installation

Faça um backup da instalação e das configurações antes de actualizar. Se a cópia foi criada a partir de uma tag, poderá estar em `detached HEAD`; não execute `git pull` como se estivesse numa branch.

```bash
cd /opt/stremio-server-webadmin
sudo git fetch origin --tags
sudo git checkout v0.1.4
sudo sh packaging/stremio-webadmin-setup.sh
```

Se existirem ficheiros locais modificados, guarde-os antes de mudar de tag. O instalador não deve apagar a configuração anterior. Nunca execute `docker compose down -v` para uma actualização de rotina: isso pode eliminar downloads, preferências e SRT traduzidos. A sincronização com um Gateway é opcional e requer política de host, lease válido e confiança TLS.



### Install from a release package

Pode instalar o pacote disponível na [release v0.1.4](https://github.com/emanique/stremiowebadmin/releases/tag/v0.1.4):

```bash
sudo apt install ./stremio-webadmin_0.1.4_all.deb
sudo stremio-webadmin-setup
```

O pacote inclui o código-fonte e compila as imagens no próprio host. Consulte `SHA256SUMS` antes da instalação. Os modelos offline são separados, não integram o pacote e exigem provisionamento opcional. As imagens não são distribuídas como artefactos GHCR imutáveis.



### Default endpoints

| Service | Address |
|---|---|
| HTTPS WebAdmin | `https://HOST_IP/` |
| WebPlayer | `http://HOST_IP:8080/` |
| Stremio API | `http://HOST_IP:11470/` |
| Stremio HTTPS | `https://HOST_IP:12470/` |
| BitTorrent | The effective TCP/UDP port in `.env`, verified against Docker and `/netcheck.json` |

These are defaults, not a guarantee that a firewall or reverse proxy permits external access.

## 📺 IPTV e tradução offline: funcionalidades v0.1.4

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

---

## 🔒 Security

**The standalone WebAdmin has privileged Docker management capabilities. Corrected new installs use HTTPS without a login only for the detected trusted LAN, enforced by Nginx `allow`/`deny`; the direct WebAdmin remains on loopback.** Restrict TCP 443 to a trusted administrator LAN and apply host firewall rules. Treat the Docker socket, environment files, provider credentials and API tokens as privileged secrets.

For a remote VPN Gateway, use TLS trust validation, a dedicated read-only token, the expected host MAC/IP and policy identity, a current lease, and verified matching TCP/UDP port values. Never embed private keys or tokens in examples, screenshots or release packages.

## 🧪 Release readiness

**v0.1.4 — validação de código:** 1.735 testes passaram, 2 foram ignorados e um teste dependente de torrent/rede externos ficou fora da execução alargada; 22 testes focados passaram após promoção para `main`. Os pacotes Debian e fonte foram gerados e os respectivos checksums SHA-256 verificados.

**Pendente para aceitação operacional:** fresh install completo num host independente, reinício e persistência, WebPlayer e App nativa em dispositivos distintos, IPTV com fontes reais e diferentes codecs, retenção e reutilização de legendas, dispositivos com VAAPI/NVIDIA quando disponíveis, e conectividade externa do Gateway se aplicável.

**Publicação:** o repositório privado `emanique/StremioServerWebAdmin` contém desenvolvimento e QA; o repositório público é a distribuição autónoma. Modelos binários, tokens, playlists privadas, certificados e cache não integram Git. O código público `main` pode receber correcções documentais depois da tag; para reprodução exacta de v0.1.4 use sempre a tag e os ficheiros anexados à release.



## 📸 Screenshot contribution

The gallery filenames above are stable in **both** repositories. Add screenshots to `docs/screenshots/` in the private DEV repository first, with sensitive information removed; the approved set is promoted unchanged to the public repo together with the corresponding README. Do not use screenshot links before the image files exist.

## 📄 Provenance and licensing

This is a standalone derivative distribution. It tracks Stremio Core and a WebAdmin platform as separately versioned components. The original reference project is [emmanique/stremio-webadmin](https://github.com/emmanique/stremio-webadmin); code provenance and the applicable licence must remain intact in the first source release.

---

<sub>Maintained for self-hosted deployment. Screenshots sanitizadas e resultados de fresh install serão publicados após validação funcional independente.</sub>


### Diagnóstico de streams e disponibilidade de legendas

O WebAdmin disponibiliza **Streams & Cache → Detalhes** para consultar os peers,
velocidade de download/upload e bytes transferidos directamente no Core.
O endpoint administrativo é apenas de leitura e omite IPs dos peers.
A App Stremio é um cliente independente: este desenvolvimento não modifica o
painel Statistics nativo da App nem força a sua apresentação.

O tradutor só anuncia legendas se existir um SRT/VTT local, uma faixa de texto
incorporada, uma fonte capturada na cache do hash, ou um fornecedor de legendas
configurado que devolva uma fonte compatível. Quando o fornecedor responde
com uma lista vazia, nenhum modelo de tradução consegue criar uma legenda
sem texto de origem. Podem configurar-se até três manifestos HTTPS através do
parâmetro `subtitle_source_addons`, respeitando as necessidades de autenticação
do fornecedor. As legendas originais e o aviso introdutório da tradução
mantêm-se intactos.
