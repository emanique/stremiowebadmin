#!/usr/bin/env bash
# Read-only QA; never accesses VPN profiles, secrets, .env, cookies or logs.
set -Eeuo pipefail
umask 077
ROLE=''
STREMIO_IP=''
GATEWAY_IP=''
WAN_HOST=''
usage() { echo "Usage: bash $0 --role gateway|stremio|proxy [--stremio-ip IP] [--gateway-ip IP] [--wan-host DNS]"; }
while (($#)); do
  case "$1" in
    --role) ROLE="$2"; shift 2;;
    --stremio-ip) STREMIO_IP="$2"; shift 2;;
    --gateway-ip) GATEWAY_IP="$2"; shift 2;;
    --wan-host) WAN_HOST="$2"; shift 2;;
    --help|-h) usage; exit 0;;
    *) usage >&2; exit 2;;
  esac
done
case "$ROLE" in gateway|stremio|proxy) ;; *) usage >&2; exit 2;; esac
python3 - "$STREMIO_IP" "$GATEWAY_IP" "$WAN_HOST" <<'PY'
import ipaddress, re, sys
for value in sys.argv[1:3]:
    if value:
        ip = ipaddress.IPv4Address(value)
        if ip.is_loopback or ip.is_multicast or ip.is_unspecified:
            raise SystemExit('LAN IP invalid')
if sys.argv[3] and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}',sys.argv[3]):
    raise SystemExit('WAN hostname invalid')
PY
OUT=/var/tmp/fresh-install-qa
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DIR="$OUT/$ROLE-$STAMP"
mkdir -p "$DIR"
chmod 700 "$DIR"
record() {
  local label="$1"; shift
  printf 'TEST=%s\n' "$label"
  set +e
  "$@" 2>&1
  local code=$?
  set -e
  echo "EXIT=$code"
}
probe() {
  local label="$1" url="$2"
  echo "TEST=$label"
  curl -sS --connect-timeout 4 --max-time 12 --output /dev/null \
       --write-out 'HTTP=%{http_code} TLS_VERIFY=%{ssl_verify_result}\n' \
       "$url" || echo 'PROBE_FAILED=1'
}
{
  echo "ROLE=$ROLE"
  echo "TIMESTAMP_UTC=$(date -u -Iseconds)"
  echo "HOSTNAME=$(hostname)"
  record OS cat /etc/os-release
  record IPv4 ip -4 -brief addr
  record routes ip -4 route
  record docker docker --version
  record compose docker compose version
  record buildx docker buildx version
  record containers docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'
} >"$DIR/host.txt" 2>&1
case "$ROLE" in
gateway)
  {
    record policy_agent systemctl is-active vpngw-policy-agent.service
    record dns_guard systemctl is-active vpngw-pihole-dns-acl.service
    record discovery_timer systemctl is-enabled vpngw-discovery.timer
    if [[ -n "$GATEWAY_IP" ]]; then
      probe management "https://$GATEWAY_IP/"
      probe pihole "https://$GATEWAY_IP:8443/"
      probe unauthenticated_status "https://$GATEWAY_IP:9443/"
    fi
  } >"$DIR/gateway.txt" 2>&1
  ;;
stremio)
  {
    record reconcile_timer systemctl is-enabled stremio-webadmin-reconcile.timer
    if [[ -n "$STREMIO_IP" ]]; then
      probe core_health "http://$STREMIO_IP:11470/health"
      probe webplayer "http://$STREMIO_IP:8080/"
      probe https_settings "https://$STREMIO_IP:12470/settings"
    fi
  } >"$DIR/stremio.txt" 2>&1
  ;;
proxy)
  {
    record nginx_config nginx -t
    record nginx_running systemctl is-active nginx
    record session_running systemctl is-active stremioproxywan-session.service
    if [[ -n "$WAN_HOST" ]]; then
      probe unauthenticated_wan "https://$WAN_HOST/"
    fi
  } >"$DIR/proxy.txt" 2>&1
  ;;
esac
cat >"$DIR/README.txt" <<'EOF'
Read-only diagnostics. Self-signed TLS can be rejected as expected.
Do not use -k to bypass TLS in compliance checks.
Do not include environment, tokens, passwords, VPN profiles or logs.
Review hostnames/IPs before transferring this archive.
This probe is not proof of playback, NAT reachability or rollback.
EOF
tar -czf "$OUT/$ROLE-$STAMP.tar.gz" -C "$OUT" "$ROLE-$STAMP"
chmod 600 "$OUT/$ROLE-$STAMP.tar.gz"
echo "EVIDENCE=$OUT/$ROLE-$STAMP.tar.gz"
