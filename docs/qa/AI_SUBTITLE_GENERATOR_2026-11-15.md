# AI Subtitle Generator — QA isolada — 2026-11-15

## Objectivo
Validar a cadeia local de reconhecimento de áudio e geração de legendas em
Português (Portugal), preservando o Core e WebAdmin em produção.

## Estado
**Parcialmente validado — NO-GO para produção.**

- Ambiente: contentor descartável com rede desligada, limite 2 vCPU / 3 GiB.
- Host: dispositivo stremioprod (não se alteraram serviços, Compose, volumes activos ou portas).
- Fonte: amostra oficial de fala JFK em WAV 16 kHz mono, não um filme/episódio real.
- Whisper: `whisper.cpp` tag `v1.7.6`, compilação CPU em Ubuntu 22.04.
- Binário SHA-256: `bcc717e3a67a05ec8a4da6aaac8660c56932d30295eff396943382907873aebf`.
- Modelo de qualidade proposto: `ggml-small.bin` multilingue, SHA-256
  `1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b`
  (aproximadamente 466 MiB).
- Modelo rápido de validação: `ggml-tiny.bin`, SHA-256
  `be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21`.
- Modelo offline de tradução existente: `en_pt` CTranslate2.

## Resultados confirmados
1. `whisper-cli -oj` reconheceu inglês da amostra e devolveu timestamps em
   milissegundos; 1 segmento, 00:00:00,000–00:00:10,560 (tiny).
2. `json_to_srt()` converteu correctamente o formato JSON real.
3. `translate_many(en, pt-PT)` produziu português com timestamps mantidos.
4. A execução real da função `generate()` em Docker, com extracção FFmpeg,
   Whisper (tiny e small), tradução local e cache SRT, terminou com sucesso.
5. O modelo `small` produziu o SRT com timestamp
   `00:00:00,000 --> 00:00:11,000` e texto:
   «E assim, meus compatriotas americanos, não perguntem o que o vosso país
   pode fazer por vós, perguntem o que podem fazer pelo vosso país.»
6. O identificador do addon foi validado por teste isolado:
   `AI Subtitle Generator · Português (pt-PT)`, `lang=pt-PT`.
7. Incompletude de ficheiro, deduplicação e backoff de falhas foram cobertos
   por testes simulados.

## Segundo ensaio: detecção automática de MKV
- Foi criado um ficheiro `jfk-speech.mkv` com áudio PCM, exclusivamente na
  cache QA, utilizando FFmpeg.
- Um handle libtorrent de teste simulou `file_complete(0) == True`, com
  `infohash` isolado do primeiro ensaio. A verificação real das peças de
  um torrent continua por validar.
- O método `AutoSubtitleWorker.scan_once()` invocou o pipeline completo
  usando o modelo `ggml-small.bin`, gerou um SRT português associado ao
  `infohash` e índice do ficheiro, e não repetiu a geração na segunda
  passagem.
- `delete_hash()` removeu o SRT da fonte de teste, sem afectar dois SRT
  pertencentes a outro `infohash`.
- Ambos os contentores de produção permaneceram em estado saudável.

## Incidente detectado e correcção
O binário CPU inicial precisava de `libgomp.so.1`, inexistente na imagem
de runtime actual. A execução real passou após disponibilizar temporariamente
a biblioteca do Ubuntu 22.04 num bind mount, apenas no contentor QA.
O Dockerfile de `develop` passou a instalar `libgomp1`; a nova imagem final
ainda **não** foi construída nem testada como artefacto de release.

## Bloqueios para aprovação
- `develop` divergiu de `main` (no momento da comparação: 19 commits à
  frente e 66 atrás). Reconciliar sem perder correcções de Core/cache.
- Testar com ficheiro de filme a 100% confirmado por libtorrent e validar
  selecção de faixa áudio correcta, incluindo packs com vários ficheiros.
- Validar produção/remoção do SRT mediante eliminação explícita e automática
  da cache. A branch `main` tem uma via `purge_title()` que `develop` ainda
  não incorpora; o merge deverá integrar este novo artefacto nessa via.
- Testar o manifesto e selecção do addon na aplicação Stremio nativa
  e no WebPlayer, incluindo recarregamento quando a legenda surge.
- Testar desempenho em filme completo, limitação de I/O/CPU e cancelamento
  de uma transcrição quando a fonte é apagada; não assumir que toda a duração
  de processamento é adequada à reprodução simultânea.
- Verificar variantes linguísticas pt-PT (um modelo `en_pt` não é, por si,
  prova de localização europeia consistente).
- Reconstruir uma imagem de QA e executar regressões de download, GPU,
  IPTV, Subtitle Translator e WebAdmin antes de fazer upgrade.
- Integrar controlos operacionais completos no WebAdmin.

## Decisão
Manter `STREMIOSRV_AI_SUBTITLES_ENABLED=false` por defeito.
Não promover para `main` ou repositório público nesta fase.
Os modelos/binários de QA estão em diretórios temporários locais, não no Git.

## Integração segura sobre a main actual
- Branch: `feature/ai-subtitles-main-integration`, criada a partir da
  `main` actual, em vez de unir directamente a `develop` divergente.
- Verificação da comparação: branch de integração sem commits em atraso
  relativamente à `main` no momento da criação.
- Funções de cache na `main`: `purge_title()` conserva o seu desenho
  corrente e chama `delete_ai_subtitles()` após eliminar o título.
- QA actual: cinco rotas registadas sobre o módulo `app.py` moderno;
  remoção do título e dos SRT associada a um hash, preservando SRT de outro.
- WebAdmin: entrada `ai-subtitle-generator` disponível no inventário;
  link de instalação apenas anunciado quando HTTPS e requisitos de runtime
  estiverem confirmados.
- Clean install: `.env.example`, `compose.yaml` e
  `scripts/provision-ai-subtitles-runtime.sh` usam default desactivado,
  mounts read-only, modelo com SHA-256 fixo e binário com checksum explícito.
- O script de aprovisionamento passou nos testes `--install` e
  `--verify` numa pasta QA, sem alterar o Stremio activo.
- Imagem de QA `ai-subtitles-qa-core:local` derivada do Core existente
  com `libgomp1`: dependências do binário resolvidas sem overrides de
  `LD_LIBRARY_PATH`.
- O pipeline real voltou a passar nessa imagem (`ggml-small.bin`,
  FFmpeg, Whisper, tradução offline e SRT), seguido de remoção por hash.
- **Não foi construído um release imutável completo a partir da nova
  branch**; o ensaio isolado montou os módulos actualizados sobre uma imagem
  Core QA derivada da imagem de runtime.
- A produção continua inalterada, com os contentores Core/WebAdmin saudáveis.

## Pendências restantes
1. Regressão completa da suite `pytest` com source build integral da branch.
2. Instalação limpa da branch de integração em VM QA com modelos preparados.
3. Filme/episódio real a 100% verificado pela engine libtorrent (não apenas
   handle simulado), incluindo packs, faixas áudio e eliminação durante jobs.
4. Selecção real e actualização de legendas na App e WebPlayer.
5. Medições de custo CPU, disco e qualidade pt-PT em filmes longos.
6. Só após estes testes: revisão da PR, promoção a `main` e publicação.

## Testes automatizados no ambiente integrado
- 10 testes `pytest` específicos do addon (`test_ai_subtitle_*.py`):
  **10 PASS**, nenhum erro.
- Regressão existente para Subtitle Translator, fontes, embedded selection,
  HTTP e clean snapshot: **24 PASS**, nenhum erro.
- A primeira tentativa de regressão não incluía scripts de instalação no
  volume do contentor QA. Após montar os ficheiros realmente utilizados pelos
  testes, a suite passou sem mudanças no código funcional.
- Aviso não bloqueante: `StarletteDeprecationWarning` relativo a `httpx`
  no ambiente QA. Deverá ser revisto na actualização de dependências.
- Total nesta bateria: **34 testes aprovados**.
- O `pytest` de toda a aplicação, de ponta a ponta, continua pendente.

**Estado de publicação:** PR draft #2, sem merge, release NO-GO.


## Correcções adicionais após reconciliação da develop (15/11/2026)

- A publicação do ficheiro `.srt` passou a utilizar a mesma
  `cache._TITLE_PURGE_LOCK` que `cache.purge_title()`. Se a fonte foi apagada
  ou substituída durante a transcrição, não é publicada uma legenda obsoleta.
- Validam-se o inode, volume, dimensão e data de modificação da fonte antes
  da publicação atómica. Directórios de legendas apenas são criados na fase
  final, evitando órfãos quando a transcrição falha.
- Identificadores Stremio explícitos `stremiosrv:<infohash>:<file_index>`
  passam a recuperar SRT persistente mesmo após descarregamento do handle
  libtorrent, sem resolver packs por aproximação.
- A bateria dirigida do addon aumentou para **13 testes aprovados**,
  incluindo perda/substituição da fonte e recuperação sem handle.
- A cadeia REAL com `ggml-small.bin` foi repetida após as correcções:
  FFmpeg → Whisper → tradução offline → SRT pt-PT → remoção por hash,
  **PASS**, no contentor de QA isolado.
- Os contentores de produção e os ficheiros de configuração activos não
  foram alterados. A feature permanece opt-in e desligada por defeito.

**Ainda não aprovado:** download de filme real por libtorrent, cancelamento
de execução prolongada, exploração de legendas na App e WebPlayer e instalação
limpa numa VM QA independente.
