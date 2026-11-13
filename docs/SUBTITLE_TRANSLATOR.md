# Subtitle Translator Local — private DEV build

**Project status (12 November 2026):** code consolidated in private `develop`; installed and functionally QA-tested on `192.168.1.250`, **not** released or deployed to `192.168.1.200`. Independent
Stremio addon ID: `org.stremiosrv.subtitle-translator`. My Library is a
different addon and is not a functional prerequisite.

## GTSubs-style target workflow

1. Stremio requests subtitle tracks for a movie or an episode.
2. The local addon matches the current title against the **primary torrent
   cache** using library metadata or exact playback file size/name, never
   guessing from a display name alone.
3. It queries explicitly configured HTTPS subtitle provider addons (subtitle
   lookup may need Internet; **translation never calls a cloud API**).
4. The subtitle picker receives `pt-PT` translated-track choices, labelled
   by source subtitle language. **No upload via WebAdmin is required.**
5. Selecting a translated track requests its subtitle URL. The backend
   discovers/revalidates the selected source, downloads it, uses an installed
   local Argos model, and saves the result.
6. A matching cached translation is returned directly on subsequent plays,
   without downloading or translating the source again.
7. Files are stored under `<cache_root>/.subtitle-translator/<infohash>/`,
   not `<cache_root>/transcode/`, and tied to primary-cache deletion.

## Configuration

- Destination: `pt-PT`, fixed for this DEV revision.
- Source-language preference: `any`. Only languages supported by installed
  offline Argos packages can actually be translated.
- `subtitle_source_addons` is exposed in WebAdmin general settings as a
  comma-separated list of HTTPS source addon **manifest URLs**.
- Example setting: `https://your-subtitle-addon.example/manifest.json`.
  This is an example, **not** a working source.
- The server makes a bounded, public-IP-only request through its guarded
  outbound proxy for each subtitle provider response and selected source.
  The server never accepts an arbitrary client-provided upstream URL.
- The stored format currently supports SRT and basic WebVTT conversion.
  ASS/SSA and complex VTT styling still require validation.
- Installing Argos Translate and compatible offline models is a separate
  packaging task. A generic `pt` model does **not** guarantee Portuguese
  of Portugal morphology or vocabulary. Do not claim strict pt-PT until
  linguistic tests pass.

## Security and lifecycle

- Independent opaque addon token with the same private-network address
  policy as other local addons.
- Validated 40-digit infohash; no direct client-provided media paths.
- No translation on a subtitle-list request. Actual translation occurs on
  selected subtitle download only.
- Original subtitle is not overwritten.
- Source URLs are rediscovered and checked against the requested track
  identity before first download; cached translation responses need no
  provider access.
- Persistent translation directory is protected against main cache eviction
  while a source title still exists.
- Primary-cache `cache._remove` performs child translation deletion after
  successful media removal. A read-only listing never deletes translations.
- No modification to the My Library addon manifest or routes.

## QA performed (2026-11-12)

On a separate Debian machine in `/tmp/subtitle-translator-qa-20261112`:
`python3 -m pytest -q tests/test_subtitle_translator.py
tests/test_subtitle_translator_sources.py` — **11 passed**.
Python syntax compilation passed for staged modules. The source-discovery
tests use a mock provider and do not make network requests.

This confirms parser logic, cache identity/reuse/removal, source discovery
and input filtering, **not** playback, translation quality or full runtime
integration.

## Remaining release blockers

- Install and verify at least one source->Portuguese offline language model
  in the actual runtime image and pin its package/model versions.
- Validate the full Stremio subtitle discovery/selection route against
  real source addon endpoints and a clean title in the principal cache.
- Verify first-fetch latency/timeout in WebPlayer, Windows and Android TV.
- Add proper ASS/SSA/VTT normalization, source encoding tests, error metrics,
  and multi-process locking or a persistent translation queue.
- Validate generic Argos Portuguese versus pt-PT output; if necessary
  select or fine-tune a suitable local model.
- Exercise full project regressions, clean install, upgrade and rollback.
- Validate deletion after natural evictions and manual title removal.

**Release policy:** never merge to `main`, publish publicly or deploy to
`192.168.1.200` until the above gates pass. A manual SRT management route
remains available for developer QA but is hidden from the WebAdmin addon
picker and is not part of the requested user experience.

## DEV validation update — 12 November 2026

The standalone subtitle service now loads through FastAPI with nine routes.
The isolated `debian` QA environment has an actual FastAPI/httpx installation
and the following four suites passed:

```
tests/test_subtitle_translator.py
tests/test_subtitle_translator_sources.py
tests/test_subtitle_translator_http.py
tests/test_subtitle_translator_normalize.py
```

**19 passed**, with one non-failing FastAPI/Starlette deprecation warning
in the isolated environment.

An actual, reachable OpenSubtitles v3 manifest and movie subtitle endpoint were
verified. For the controlled title `tt0133093` the provider returned 26
original tracks and none in English; missing English was a provider result,
not a filtering defect. Three-letter language codes (e.g. `ger`, `spa`) are
normalized for local model selection.

A real Spanish source subtitle (93,871 bytes; 1,373 timed cues) was downloaded
and normalized, then passed to a **mock translator**. A second invocation
reused the translated SRT without invoking the mock translator. This
verifies real subtitle ingestion/persistence, **not** actual machine
translation quality or latency.

WebAdmin `subtitle_source_addons` is now checked on save and read from the
current persistent config JSON on each lookup. The default verified provider
for new installs is the official
`https://opensubtitles-v3.strem.io/manifest.json`; operators may override
or disable it. The translation backend never sends subtitle text to an
external translator.

The attempt to add the optional Argos library to the isolated QA Python
environment was **stopped** after a long, resource-intensive dependency
download. No Argos model has been installed or measured, and the server
runtime image has not been modified.

The following release blockers remain:
1. A reproducible, CPU-conscious local Argos/model package build, ideally
   with pinned CPU-only dependencies and model checksums.
2. Actual `en/es/fr/de -> pt-PT` inference, output linguistic review and
   first-selection latency/timeout measurements. Generic Argos `pt` is
   not a verified `pt-PT` model.
3. Real Stremio WebPlayer/native-app subtitle selection tests and rollback.
4. Hardening subtitle source request timeouts, multi-worker cache locking,
   robust orphan cleanup and conversion of more subtitle formats.
5. Full project regression tests and clean-install/upgrade checks.

No deployment to `192.168.1.200`, public release or merge to `main`.



## Hardening and final QA — 2026-11-12

- **21 tests passed** across four targeted suites on isolated Debian QA.
- The manual SRT upload, management page and cached title browser now return
  HTTP 404 by default. The test-only UI requires explicit environment
  `STREMIOSRV_SUBTITLE_TRANSLATOR_QA=1`.
- The advertised subtitle choices are now filtered by local translation-model
  availability. Unsupported source languages are never offered as callable
  pt-PT tracks.
- Model resolution is cached per language pair, avoiding repeated discovery
  for every SRT cue. This is a performance improvement, not a substitute
  for actual CPU latency testing.
- Default subtitle source: verified official
  `https://opensubtitles-v3.strem.io/manifest.json`.
- Local model package installation for Argos was **not completed**: a lengthy
  isolated dependency download was deliberately terminated and remains a
  release blocker. No package was installed on production servers.

**Release state remains DEV-only.** The 21 tests validate simulated translation,
source discovery, HTTP routing, conversion, and cache persistence. They do not
prove pt-PT output quality, actual Argos inference, client timeout compatibility,
or full application regression.


## CPU-only inference integrated — 2026-11-12

The isolated Debian QA host successfully performed **real** Spanish ->
Portuguese translation with CTranslate2 4.9.0 and SentencePiece 0.2.3.
This path does not require the heavy PyTorch/Stanza stack. The actual
`es_pt` model was retrieved from the Argos package index and the compressed
archive had SHA-256:

`c772951dee3917f799b701bde0612331f54e1aa09d709dd057bee6b41bb04936`

The archive is about 67.5 MB. The extracted inference model is about 70 MiB.

Real CPU outputs observed:
- `Hola, ¿cómo estás?` -> `Olá, como estás?`
- `Buenos días, necesito saber la verdad.` -> `Bom dia, preciso saber a verdade.`
- `No me gusta este plan.` -> `Eu não gosto deste plano.`

On this isolated CPU:
- Initial model loading: 0.292 seconds.
- Three individual example phrases: 0.279 seconds, excluding cold model load.
- Actual local batch backend: 100 short SRT cues took 6.068 seconds.
- Second `translate_once` call with identical title/source: persistent
  cache hit in **0.47 ms** (process-local measurement; no network).

The addon `translate_once` now prefers a locally installed
CTranslate2/SentencePiece pair and translates SRT cues in bounded batches.
Argos remains a fallback only when it has actually been provisioned.
Source options are filtered to locally available model pairs.

### Model provisioning (private DEV)

A new SHA-256-verified installer is available at
`scripts/subtitle-model-install.py`. Given the operator-downloaded archive:

```bash
python3 scripts/subtitle-model-install.py \
  --package /path/to/translate-es_pt-1_0.argosmodel \
  --destination ./models/subtitles \
  --sha256 c772951dee3917f799b701bde0612331f54e1aa09d709dd057bee6b41bb04936
```

For this pinned QA pair only, the download URL in the verified index was
`https://argos-net.com/v1/translate-es_pt-1_0.argosmodel`.
Verify source trust and digest before use. Model files are installed
outside the media cache and mounted at `/opt/stremio-subtitle-models:ro`
through `compose.yaml`.

The DEV Dockerfile installs pinned CPU-only dependencies
`ctranslate2==4.9.0` and `sentencepiece==0.2.3`. No model is downloaded
inside the Docker build or on demand during playback.

### Current regression status

The isolated targeted suite reports **29 passed, 1 skipped**.
The skipped scenario assumes the complete Argos library is absent; the
alternative CPU-only implementation is covered separately. The test suite
includes offline model installer guards, batching, cache reuse and real
FastAPI request routing. There is one non-failing test-framework
deprecation warning.

### Important unresolved constraints

- This model is generic `pt` and has **not been linguistically qualified
  as strict Portuguese of Portugal**.
- First-time full-film translation can take much longer than a player's
  subtitle request timeout; 100 short cues already took 6 seconds in QA.
  Validate actual native-client latency and decide whether a separately
  managed pre-translation job is needed.
- The only verified real inference path is Spanish -> Portuguese. Other
  source languages need separately provisioned and tested models.
- The actual production Docker build, complete Stremio native app workflow
  and clean install/rollback have not been tested.
- The pinned CPU model is not being shipped in the repository itself;
  no production installation has been attempted.


## Prioridade de origem e legendas já existentes

No selector Stremio as traduções candidatas são ordenadas:
**1.º inglês (en→pt), 2.º francês (fr→en→pt), 3.º espanhol (es→pt)**.
Só aparecem fontes com todos os modelos offline instalados. Se houver modelo
directo fr→pt, é preferido à tradução intermédia.

As fontes originais são obtidas de addons de legendas HTTPS explicitamente
configurados no WebAdmin. Se já houver um ficheiro de legenda externo com o
mesmo nome base do vídeo e sufixo `.en.srt`, `.fr.srt` ou `.es.srt`
(também `.vtt`, `.ass`, `.ssa`), esse ficheiro existente também é
apresentado como origem seleccionável. O original não é modificado.

Não existe um protocolo Stremio para ler a legenda já seleccionada **noutro
addon**. A escolha deve ser feita na opção do Subtitle Translator, que
redescobre a mesma fonte, ou num ficheiro de legenda existente junto ao vídeo.
A tradução é efectuada somente após seleccionar a versão portuguesa, ficando
na cache principal do título. As consultas de fontes podem necessitar de
Internet, mas o motor de tradução não efectua chamadas a serviços cloud.

Quando o vídeo pertence ao My Library, `stremiosrv:<infohash>[:fileIdx]`
liga o pedido ao torrent sem precisar de um rótulo IMDb. Nos pedidos IMDb,
a associação é feita por metadados previamente registados ou correspondência
inequívoca de tamanho/ficheiro comunicados pelo próprio leitor. Se não
existirem legendas originais e o filme não contiver ficheiros auxiliares,
o addon devolve uma lista vazia — não fabrica legendas.

Os URLs SRT publicitados aos clientes podem ser fixados por
`STREMIOSRV_SUBTITLE_PUBLIC_ORIGIN` para HTTPS confiável. O modelo `pt`
gera português, mas a qualidade estritamente **pt-PT** requer testes
linguísticos antes de uma garantia de produção.

## Evidence after QA installation on .250 — 12 November 2026

- Real CTranslate2/SentencePiece CPU translations en→pt, fr→en→pt and
  es→pt executed without Internet; cached SRT replay confirmed.
- 382-channel IPTV and source→Core→HLS→client playback validated separately;
  IPTV and Subtitle Translator are separate addons and cache domains.
- A real embedded English ASS track (stream 4) was enumerated on a partially
  downloaded cached film and extracted as 131 SRT cues. Extraction took
  approximately 39 seconds; it did not alter the video.
- The private HTTPS subtitle manifest listed the embedded English translation
  choice with URL on the trusted Stremio streaming TLS origin.
- Synthetic FastAPI integration tests passed for embedded track selection,
  local cached sidecars, stable subtitle identifiers, and single-translation
  reuse. The original English track is not replaced.
- Embedded image subtitles (PGS, VobSub) require OCR and are not in scope.
  A subtitle already selected in another Stremio addon cannot be read through
  the public addon protocol; this addon must offer its own original source.
- Full native app/WebPlayer translation latency, mixed client compatibility,
  extended endurance, and strict pt-PT linguistics are still unverified.
- Host migration steps and SHA-256-verified model packages are in
  `docs/UPGRADE_200.md` and `scripts/provision-subtitle-models.sh`.

The earlier DEV-only sections document historical checkpoints; this section
supersedes earlier statements that only Spanish inference was installed or
that the addon was not deployed on a QA host.
