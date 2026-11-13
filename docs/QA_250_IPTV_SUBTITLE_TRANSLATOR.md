# QA controlado — IPTV + Subtitle Translator no 192.168.1.250

Este procedimento aplica-se **somente à máquina DEV `192.168.1.250`**.
Não deve ser executado no `192.168.1.200` nem no servidor de produção.
A instalação preserva o My Library e não copia URLs M3U, tokens, nomes de
utilizador, passwords ou listas do fornecedor de outra máquina.

## Pré-condições

1. Acesso administrativo autenticado ao próprio host `.250`, por SSH ou
   Desktop Commander. O teste de rede apenas confirmou portas 22/443 abertas;
   **não confirmou autenticação SSH**.
2. Inventário do estado existente: versão/commit, `docker compose ps`,
   imagens, volumes e caminho de instalação. **Não apagar nem substituir
   volumes existentes**.
3. Confirmar recursos e a política de rollback para recuperar ambos os
   containers sem perder o estado anterior.
4. Fixar explicitamente `STREMIO_SOURCE_BRANCH=develop` e
   `STREMIO_RUNTIME_BRANCH=develop` apenas no ambiente QA. A configuração
   normal continua independente da branch principal.
5. `STREMIO_DNS_SERVER` e `IPADDRESS=192.168.1.250` devem ser obtidos do
   host. Nunca usar valores fictícios para executar o Compose real.
6. Modelos de tradução instalados apenas a partir de pacotes previamente
   verificados por SHA-256, sob `./models/subtitles` ou o caminho local
   indicado por `STREMIO_SUBTITLE_MODELS_DIR`.

## Etapas de execução

- Confirmar que o IP da máquina é `192.168.1.250`, que os containers
  existentes estão identificados e que as credenciais não são exportadas
  para o repositório ou logs.
- Fazer backup local, protegido, de metadados Docker/Compose, volumes
  persistentes e configuração original. Registar o digest das imagens.
- Usar um checkout separado do repositório privado `develop` e validar
  `docker compose config --quiet` com as variáveis reais do host.
- Confirmar antes do arranque que nenhuma porta, nome de container ou volume
  irá colidir com outra instalação não abrangida pela janela QA.
- Construir as imagens de DEV no próprio `.250`. **Não usar GitHub Actions**.
- Arrancar os serviços com a estratégia de atualização/rollback definida.
  Manter a playlist IPTV sem fornecedor configurado até introdução posterior
  pelo WebAdmin.
- Validar o Stremio base (8080, 11470, 12470), WebAdmin 443 e My Library
  *antes* de instalar os manifestos novos.
- Executar o preflight read-only:
  ```bash
  python3 scripts/qa-addon-preflight.py \
    --url https://192.168.1.250 --insecure-lab-cert
  ```
  O modo `--insecure-lab-cert` destina-se exclusivamente ao certificado
  de laboratório. Não o utilizar fora deste contexto.

## Critérios IPTV

- O cartão **IPTV via StremioSRV** surge em
  `Addons available on this server`, com ligação à aba IPTV.
- Ao activar o addon com uma playlist válida, é apresentado o manifesto.
  O URL do manifesto contém um token de instalação: não o registar em
  documentação partilhada.
- Testar uma playlist sintética `.m3u` e outra `.m3u8` no WebAdmin.
  Não importar manifests HLS `#EXT-X-*` como listas de canais.
- Verificar o número original `tvg-chno`, a ordem da playlist e a edição
  manual de posição. Ocultar individualmente um canal e confirmar que
  desaparece do catálogo e não reproduz com o job anterior.
- Restaurar o canal e confirmar que reaparece sem modificar a lista original.
- Recarregar a playlist, reiniciar apenas os serviços necessários e confirmar
  que as preferências persistem no volume local protegido.
- Não incluir informação de fornecedores reais, logins, passwords ou URLs
  de listas IPTV nos artefactos de teste e repositório.

## Critérios Subtitle Translator

- O cartão **Subtitle Translator** surge no WebAdmin, identificado como
  `org.stremiosrv.subtitle-translator`.
- Verificar o modelo local `es_pt`, montado read-only e os endpoints
  manifesto, listagem e entrega de SRT, sempre sem expor tokens nos logs.
- Escolher uma legenda espanhola num título em cache, verificar o SRT
  traduzido, voltar a reproduzir e comprovar reutilização sem nova inferência.
- Confirmar que a limpeza de FFmpeg/transcoding não elimina a tradução.
  A remoção real do título da cache principal deve eliminar as traduções
  associadas.
- Sem dispositivos CUDA compatíveis no `.250`, manter `subtitle_device=cpu`
  ou `auto` com fallback. Não alterar VAAPI ou drivers de GPU.
- Testar primeiro no WebPlayer; testes da aplicação Windows/Android só se
  consideram aprovados após reprodução real.

## Go / No-Go

**Go** apenas quando houver preflight 200, manifestos válidos, My Library
funcional, regressões HLS/VAAPI sem impacto e rollback testado. A qualidade
linguística do `pt-PT` continua dependente do modelo local; um modelo genérico
`pt` não garante equivalência.

**No-Go** se o host `.250` não puder ser autenticado, se os volumes
existentes forem desconhecidos, se existir conflito de portas/containers ou
se a instalação exigir reconfigurar o `.200`.

## Estado de 2026-11-12

`https://192.168.1.250` respondeu 200 em `/health` e `/api/addons`.
Foi encontrado apenas My Library. `/api/iptv` devolveu 404 e o Subtitle
Translator não estava anunciado. SSH devolveu falha de autenticação para
os utilizadores tentados. **Nenhuma instalação no `.250` foi executada.**
