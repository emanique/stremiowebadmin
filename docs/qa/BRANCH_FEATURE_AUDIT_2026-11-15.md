# Auditoria de branches e funcionalidades — 15/11/2026

## Inventário de branches
- `main`: referência de release e código actual, SHA `24bb7956d4547c7faf70546f257dd9e91b824aa7` no início da revisão.
- `develop`: SHA `1b9644b7a4ead4ee8fb7c10feba432696e98c759`; 22 commits à frente e 66 atrás de `main`.
- `feature/ai-subtitles-main-integration`: directamente baseada em `main`, 0 commits atrás. PR #2 permanece draft.
- PR #3, `main -> develop`, criada em modo draft para registrar a sincronização pendente, sem merge forçado.

## Features de main observadas no código e documentação
| Feature | Evidência | Estado desta auditoria |
|---|---|---|
| Stremio Core/libtorrent com HTTPS | README, componentes Core | Presente; sem E2E novo |
| WebAdmin e gestão de cache | README e testes hash purge | Presente; regressão focada aprovada |
| HLS / IPTV e limpeza FFmpeg | README e notas RC | Presente; E2E em TV pendente |
| Subtitle Translator offline pt-PT | README e testes translator | Presente; regressão focada aprovada |
| Cache por infohash e purge | API/cache, `test_hash_purge` | 13 testes focados incluídos |
| WebPlayer / Continue Watching | README | Presente; cliente real pendente |
| GPU/VAAPI com fallback | README | Presente; carga GPU não repetida |
| NAT-PMP / Gateway externo | README e processo release | Opcional; E2E externo pendente |
| Diagnóstico peers/download | release `v0.1.5-rc.5` | RC documentada, não afirmar stable |
| Pacote Debian / instalação limpa | README e processo release | Empacotamento presente; fresh install pendente |

## Features do AI Subtitle Generator
- Controlo de 100% por `file_complete(index)` antes de gerar.
- FFmpeg + Whisper multilingual `small` + tradução CTranslate2 local + SRT.
- Manifesto independente, `lang=pt-PT`, identificador visível no selector.
- Worker opt-in por variável de ambiente, processamento único, cooldown.
- Cache por `infohash`, índice de ficheiro/áudio e modelo; remoção no `purge_title`.
- Instalação offline verificável por checksums e montagens read-only.
- Todos os itens anteriores foram implementados ou validados em ensaio isolado;
  teste com torrent real e clientes Stremio continua pendente.

## Testes dirigidos durante esta auditoria
- 10 testes AI Subtitle Generator: PASS (fase anterior).
- 24 testes de regressão Subtitle Translator / clean-install: PASS (fase anterior).
- 13 testes adicionais `test_hash_purge.py` +
  `test_subtitle_translator_local_engine.py`: PASS nesta auditoria.
- Total de casos dirigidos distintos executados nestas fases: 47 PASS.
- Aviso QA: Starlette TestClient com `httpx` depreciado; não bloqueou os testes.
- Não equivale a suite integral, release nem validação em clientes reais.

## Resultado e risco
- A feature branch está actualizada com `main`.
- `develop` conserva trabalho não integrado e diverge. **Não foi
  reescrita nem forçado qualquer merge**, dado o risco de perda de conteúdo.
- A PR #3 documenta a intenção de sincronização; requer resolução de
  divergências e teste da árvore resultante antes do merge.
- PR #2 permanece draft/no-go até QA completa e aprovação.
- Produção e repositório público permanecem inalterados.
