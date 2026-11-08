# Auditoria e ensaio de instalação limpa — três máquinas

**Âmbito:** instalar exclusivamente a partir dos repositórios públicos, em VMs novas, sem certificados, passwords, Docker images ou ficheiros dos servidores anteriores. Isto é um procedimento de QA, não uma declaração de prontidão de produção.

## Topologia de exemplo

| VM | IPv4 de exemplo | Portas |
|---|---|---|
| VPN Gateway | 192.168.50.21 | 443 gestão, 53 DNS, 8443 Pi-hole, 9443 API opcional |
| Stremio Standalone | 192.168.50.22 | 8080 WebPlayer, 11470 Core, 12470 HTTPS, BT TCP/UDP |
| StremioProxyWAN | 192.168.50.23 | 443 HTTPS externo |
| Cliente | 192.168.50.24 | App nativa, browser, testes VPN |

Substituir os valores de exemplo antes de executar. O IPv4 LAN Stremio é fixo após a instalação, enquanto WAN do Proxy admite IP fixo ou DDNS. O DNS/DDNS e NAT WAN são responsabilidades externas ao Proxy. CGNAT pode impedir a exposição directa.

## Sequência e fontes

### 1. Gateway (primeiro)

Fonte: https://github.com/emmanique/vpngateway-webadmin

A versão de QA 1.0.6-rc.1 está na main com correcções posteriores à tag pública v1.0.5. Usar a revisão de código 1178cbb31c1d9ecd7d362f178f87f81ed53b7806, validada com 360 testes Python 3.12. Registar o commit exacto e **não** instalar os pacotes antigos v1.0.5 para validar a integração recente.

~~~bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/emmanique/vpngateway-webadmin.git
cd vpngateway-webadmin
git checkout 1178cbb31c1d9ecd7d362f178f87f81ed53b7806
git rev-parse HEAD
sudo ./start.sh --lan-interface INTERFACE_LAN --lan-ip IP_GATEWAY
~~~

O instalador suporta os nomes de pacote Compose para Debian 13/Ubuntu, exige Buildx, cria password Pi-hole aleatória em deploy/.env (0600), sem sobrescrever instalações anteriores. Validar topologia, reboot, acesso HTTPS, DNS, WireGuard/OpenVPN e Direct/VPN/Bloqueado com MAC associado.

### 2. Stremio Standalone

Fonte: https://github.com/emanique/stremiowebadmin/releases/tag/v0.1.3-rc.6

~~~bash
sudo apt-get update && sudo apt-get install -y git
sudo git clone --branch v0.1.3-rc.6 --depth 1 https://github.com/emanique/stremiowebadmin.git /opt/stremio-server-webadmin
sudo sh /opt/stremio-server-webadmin/packaging/stremio-webadmin-setup.sh
~~~

O pacote .deb desta release instala código-fonte e constrói as imagens localmente; não pressupor imagens GHCR. Verificar containers healthy, HTTPS WebAdmin autenticado, Core /health, WebPlayer, HTTPS interno, reboot, reprodução, seek, legendas, IPTV e porta BT TCP/UDP actual. VAAPI só passa em hardware compatível.

### 3. Integração Gateway → Stremio (opt-in)

O Gateway pode ter IP de gestão distinto do router predefinido Stremio. O bootstrap público é servido na gestão HTTPS porta 443. A API de estado autenticada 9443 é separada e não é activada automaticamente. Antes de instalar o bootstrap no Stremio, validar fingerprint da CA pública do Gateway por canal confiável e verificar SHA-256 do pacote.

Executar bootstrap.sh --verify-only primeiro, depois --install com MEDIAHUB_PORT_SYNC_GATEWAY_IP e policy ID real. Provisionar token de leitura 0600 fora do Git. Só depois activar timer. Uma rotação real de NAT-PMP deve actualizar observação, reconciliar porta Stremio, validar health e rollback, sem o observador executar Docker.

Consultar https://github.com/emmanique/vpngateway-webadmin/blob/main/docs/mediahub-sync-explicit-gateway.md

### 4. ProxyWAN (último)

Fonte: https://github.com/emanique/stremioproxywan/tree/main/dist/v0.1.2

Verificar SHA256SUMS antes de instalar o .deb.

~~~bash
sudo apt install ./stremioproxywan_0.1.2_all.deb
sudo stremioproxywan configure --set wan_host=SEU_DOMINIO --set wan_address_mode=ddns --set lan_host=IP_STREMIO --set backend_port=12470 --set backend_identity_mode=stremio_auto --set wan_tls_mode=selfsigned --set http_policy=disabled
sudo stremioproxywan user add admin
sudo stremioproxywan discover
sudo stremioproxywan check
sudo stremioproxywan apply
~~~

Pode usar wan_tls_mode=trusted com wan_cert/wan_key adequados. Autoassinado na WAN requer confiança do cliente, mas **não** elimina validação TLS do Stremio interno. Proxy descobre SAN e constrói nome IP-com-hífens + wildcard DNS do certificado. Sem observação periódica do IP LAN fixo.

## Gates Go/No-Go

| Gate | Evidência |
|---|---|
| Origem | commit/tag, checksum, zero imagens/secrets herdados |
| Rede | interface, gateway, DNS e NAT adequados |
| Serviços | health, WebAdmin, certificados, reboot |
| VPN | Direct/VPN/Blocked, WireGuard/OpenVPN, kill-switch, DNS |
| Portas | NAT-PMP real, TCP/UDP, rotação e rollback |
| Streaming | App nativa, WebPlayer, HLS, IPTV, seek, áudio, legendas |
| WAN | login, 401 sem sessão, HLS completo, DDNS/TLS |
| Recuperação | backup, restore e rollback sem perda de configuração |

**NO-GO para GA** até executar estas provas nas novas máquinas. O código e as suites unitárias não substituem o teste físico de rede, VPN e playback.

## Recolha automática de evidências (leitura apenas)

Depois da instalação de cada VM, utilizar o script público scripts/qa-three-server-evidence.sh. Este script **não** altera serviços nem recolhe .env, certificados privados, tokens, passwords, perfis VPN ou logs. Gera um arquivo local com estado de rede, Docker, systemd e sondas HTTP/TLS, em /var/tmp/fresh-install-qa, com permissões restritas. Rever os IP/hostnames antes de partilhar.

~~~bash
# Executar na nova máquina Gateway:
bash scripts/qa-three-server-evidence.sh --role gateway --gateway-ip IP_NOVO_GATEWAY

# Executar na nova máquina Stremio:
bash scripts/qa-three-server-evidence.sh --role stremio --stremio-ip IP_NOVO_STREMIO

# Executar na nova máquina ProxyWAN:
bash scripts/qa-three-server-evidence.sh --role proxy --wan-host DOMINIO_WAN
~~~

O script está no repositório público Stremio. Pode ser obtido num clone independente, mesmo quando executado no Gateway ou Proxy. Se for executado sem privilégios Docker, o resultado será registado como não verificado. Certificados autoassinados podem falhar correctamente as sondas TLS; não desligar a validação por conveniência. O estado de reprodução de filmes, NAT e rollback exige testes funcionais adicionais, não está incluído neste script.
