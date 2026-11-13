# Upgrade controlado — StremioServerWebAdmin no 192.168.1.200

**Estado:** candidato DEV privado no ramo `develop`; não está autorizado publicar em
`main` nem no repositório público. Os ficheiros de QA de `.250` são apenas
evidência de validação, não são o instalador final.

## 1. Integridade do âmbito

Funcionalidades que deverão ficar disponíveis no `.200` após a migração:
- IPTV local: URL HTTPS ou ficheiro M3U/M3U8, 382 canais no cenário de teste,
  numeração/ordem manual, ocultar/restaurar, catálogo de Stremio paginado,
  transmissão servida por Core/HLS e transcoding FFmpeg/VAAPI;
- monitorização IPTV por nome do canal, codecs, clientes, workload, FPS e carga;
- My Library e cache principal preservados;
- Subtitle Translator independente, descoberta de fontes HTTPS configuradas,
  extracção de legendas internas **textuais** ASS/SRT/VTT (não PGS/bitmap),
  externas junto ao vídeo e fontes remotas, tradução **on demand** e cache
  principal associada ao título;
- prioridade **EN→PT**, **FR→EN→PT** e **ES→PT**, executada localmente no CPU;
- WebAdmin no HTTPS 443 e streaming no HTTPS 12470, com GPU e porta BT existentes.

A migração não muda a topologia, gateway, VPN, endereços, playlists/credenciais,
nem deve inventar nova configuração no servidor de origem.

## 2. Evidências e riscos por resolver

O fluxo IPTV foi validado funcionalmente no `.250`. A tradução local dos
três idiomas e a extracção real da faixa inglesa ASS foram testadas. Porém:
- Não foi demonstrada estabilidade de reprodução durante 24 h, nem falha de ISP,
  fornecedor IPTV ou retoma após reboot;
- tempo de extracção de uma faixa num torrent incompleto foi ~39 s. Uma app
  Stremio pode desistir da selecção antes da tradução de um filme completo;
- o modelo `pt` não garante variante linguística portuguesa europeia;
- um host `.200` com erros de disco/memória/rede deve ser diagnosticado e
  salvaguardado antes do upgrade; software novo não corrige hardware avariado;
- versões de Core não devem ser proclamadas 1.6.48 apenas porque foram alvo
  de desenvolvimento: confirmar `SERVER_VERSION` e o binário instalado.

**Critérios para avançar:** cópia íntegra, recursos suficientes, imagens
reproduzíveis, DNS/certificados correctos, VAAPI e BT existentes identificados,
plano de retorno validado, janela de indisponibilidade autorizada.

## 3. Recolha no servidor `.200`, ANTES de o desligar

Requer sessão root autenticada no próprio `.200`. O Desktop Commander da
`.200` poderá estar desligado; não copiar dados de configuração através do
GitHub ou de URLs públicos.

Com os containers ainda a funcionar, numa cópia limpa de `develop`:

```bash
python3 scripts/preflight-upgrade.py --target-ip 192.168.1.200 \
  --output /root/stremio-upgrade-preflight.json
```

O inventário omite valores de variáveis, playlists e segredos. Confirmar
capacidade livre e os volumes Core `/root/.stremio-server`, Core `/config`,
WebAdmin `/config`, WebAdmin `/data`. Recolher SMART, erros de I/O e
registos de kernel à parte se houver instabilidade.

Antes de parar, recolher IDs de imagens e garantir que as imagens antigas
continuarão disponíveis para rollback (não executar `docker image prune`):

```bash
docker inspect stremio-libtorrent-server stremio-webadmin \
  --format '{{.Name}} {{.Image}} {{.Config.Image}}'
```

### Backup consistente

Interromper os clientes de reprodução, parar o reconciliador se existir, e
parar os dois containers. O script recusa criar snapshots de volumes em uso:

```bash
systemctl stop stremio-webadmin-reconcile.timer 2>/dev/null || true
docker stop stremio-webadmin stremio-libtorrent-server
python3 scripts/snapshot-before-upgrade.py --target-ip 192.168.1.200
```

A saída indica a pasta privada `/var/backups/stremio-upgrade-YYYYMMDD-HHMMSS`
com `manifest.json`, arquivos com SHA-256 e ficheiros de configuração.
**Não enviar essa pasta para o GitHub, nem para chats.** Exportar uma
cópia segura para outro armazenamento físico antes de desligar o `.200`.
O snapshot da cache pode ser grande: verificar espaço antes de iniciar.

Se faltar espaço, não ignorar silenciosamente a cache. `--exclude-cache`
é opção excepcional, só quando a perda de todos os torrents e traduções
for expressamente aceite.

## 4. Preparar a nova instalação — SEM copiar credenciais para o repositório

Clonar uma cópia independente de `develop`; guardar o commit com
`git rev-parse HEAD` no registo de alteração. Reutilizar os dados originais
apenas depois de identificar os volumes e o nome do Compose project. Não
sobrepor automaticamente `.env`, playlists e preferências por valores de
laboratório do `.250`.

As imagens finais são construídas com os `Dockerfile` **versionados** do
repositório, não com `Dockerfile.qa250` ou `Dockerfile.subtitle-overlay`.
A instalação Python usa CTranslate2 4.9.0 e SentencePiece 0.2.3.
No `.200` instável, preferir build num host de QA saudável, validação de
imagem/digest e `docker save/load` por canal controlado.

No host de QA `.250` podem ser preparadas duas imagens independentes
`stremioserverwebadmin-core:upgrade200-qa` e
`stremioserverwebadmin-webadmin:upgrade200-qa`. O bundle de imagens
é **apenas local**, fora do Git, acompanhado de `sha256sum`; não inclui
playlists, modelos nem dados dos utilizadores:

```bash
# Após cópia autorizada do bundle para o .200, com os containers antigos
# já salvaguardados e o checksum verificado:
sha256sum -c images-upgrade200-qa.sha256
docker load -i images-upgrade200-qa.tar.gz
docker image inspect stremioserverwebadmin-core:upgrade200-qa
docker image inspect stremioserverwebadmin-webadmin:upgrade200-qa
# Retag para os nomes esperados pelo Compose *apenas no cutover*,
# preservando antes os IDs/tags antigos para rollback:
docker tag stremioserverwebadmin-core:upgrade200-qa stremioserverwebadmin-core:dev
docker tag stremioserverwebadmin-webadmin:upgrade200-qa stremioserverwebadmin-webadmin:dev
```

**Aviso:** o `.200` pode ter directórios, binds e versões diferentes dos
do `.250`; não executar o último bloco sem confrontar o inventário, a
configuração existente e a possibilidade de restaurar as imagens anteriores. Não introduzir
`--pull` ou actualizações de drivers no cutover.

Modelos offline: copiar os três `.argosmodel` de uma fonte confiável para
`models/downloads/` (fora do Git), ou fazer o download verificado
explicitamente numa máquina apta. Instalar pelo script versionado:

```bash
bash scripts/provision-subtitle-models.sh --install
# alternativa explícita com Internet (SHA-256 verificado):
# bash scripts/provision-subtitle-models.sh --download-and-install
```

Pares esperados:
- `en_pt` SHA-256 `0c5350a2fa5b923de1346edc0d42e08e38bab2e33cede3a0b9a48eb4281ad8a9`
- `fr_en` SHA-256 `3b3052fee6bb1e8e8e632a26a723eb2a2c7710dfe73ba61ffd9b83e85d4f14c1`
- `es_pt` SHA-256 `c772951dee3917f799b701bde0612331f54e1aa09d709dd057bee6b41bb04936`

Não adicionar esses binários, `.env`, `.m3u`, `.m3u8`, `.srt`,
certificados, tokens, usernames ou passwords ao Git.

## 5. Configuração específica do `.200`

Apenas depois de confirmar o certificado HTTPS:

```bash
curl -fSs -o /dev/null \
  https://192-168-1-200.519b6502d940.stremio.rocks:12470/health
```

Conservar ainda o alinhamento do WebAdmin com a fonte **privada**
(`STREMIO_SOURCE_REPO=https://github.com/emanique/StremioServerWebAdmin`,
`STREMIO_SOURCE_BRANCH=develop` e `STREMIO_RUNTIME_BRANCH=develop`);
se a instalação anterior apontar para `main`/repositório público, rever
e actualizar **apenas essas variáveis**. Sem isto, o actualizador poderá
reverter o Core para um ramo errado.

Adicionar, **sem substituir o restante `.env`**:

```dotenv
STREMIOSRV_IPTV_PUBLIC_ORIGIN=https://192-168-1-200.519b6502d940.stremio.rocks:12470
STREMIOSRV_SUBTITLE_PUBLIC_ORIGIN=https://192-168-1-200.519b6502d940.stremio.rocks:12470
STREMIOSRV_SUBTITLE_DEVICE=cpu
STREMIOSRV_SUBTITLE_CPU_THREADS=2
STREMIOSRV_SUBTITLE_MODELS_DIR=./models/subtitles
```

O WebAdmin fica na porta 443 via Nginx host; manter os binds originais
8080, 11470, 12470 e TCP/UDP BT. Conservar as variáveis de autenticação e
porta NAT-PMP que **já existiam**. O DNS/HTTPS não deve ser copiado de `.250`.

`STREMIOSRV_SUBTITLE_SOURCE_ADDONS`/definições do WebAdmin são introduzidos
no servidor pela gestão local, **não** comprometidos no código. A ligação de
addons My Library / IPTV / Translator deve usar o DNS e certificado do `.200`.

## 6. Testes Go/No-Go do upgrade

Depois da aplicação controlada das imagens e do retorno do reconciliador,
registar evidências:
1. Core e WebAdmin `healthy`, WebAdmin 443 e Core 11470/12470 com 200;
2. Estado do Core, cache anterior, My Library, recomeço de playback e BT
   mapeada pelo gateway sem alteração da porta de escuta;
3. Intel/NVIDIA/CPU tal como previamente configurado, FFmpeg e VAAPI
   efectivamente disponíveis (não apenas um indicador visual);
4. IPTV: URL e ficheiro, catálogo paginado, primeiro canal reproduzido, job
   e nome na monitorização, retoma, sem expor credenciais;
5. Subtitle Translator: três modelos visíveis, manifesto com HTTPS válido,
   legendas nativas/externas existentes, opção pt-PT, seleccionar e
   obter SRT, reutilização da cache, tempos preservados;
6. ausência de novas falhas de I/O, OOM, restarts e conflitos de porta;
7. após reboot, `start.sh`, reconciliação, volumes, modelos e GPU persistem.

**No-Go:** erro de I/O, volumes em falta, novo endereço a servir HTTP em
vez de HTTPS, modelos ausentes, erro de My Library ou perda da BT/VAAPI.
Nesses casos, desligar o novo serviço e restaurar imagens e configuração
originais usando o manifesto de snapshot. Não destruir o backup nem usar
`docker compose down -v`. A reversão de volumes de cache deve ser uma
decisão separada: pode descartar alterações posteriores ao snapshot.

## 7. Estado de execução

Este documento **não** afirma que o upgrade do `.200` foi aplicado.
Foi preparado para execução quando houver acesso de administração ao host,
backup externo confirmado e aprovação da janela. O `.250` permanece a
instância QA e deve ser mantido intacto até ao aceite no `.200`.
