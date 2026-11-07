#!/usr/bin/env bash
# Non-destructive DIRECT smoke contract for a running local Compose deployment.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
source_host="${IPADDRESS:-}"
if [ -z "$source_host" ]; then
  source_host="$(sed -n 's/^IPADDRESS=//p' .env 2>/dev/null | tail -1)"
fi
[ -n "$source_host" ] || { echo "Missing IPADDRESS; run sh start.sh config first" >&2; exit 1; }
pass=0
check() {
  local label="$1"; shift
  if "$@"; then
    echo "PASS $label"; pass=$((pass + 1))
  else
    echo "FAIL $label" >&2
    exit 1
  fi
}
http_ok(){ curl -ksS --max-time 12 -o /dev/null -w '%{http_code}' "$1" | grep -qx 200; }
json_ok(){ curl -ksS --max-time 18 "$1" | python3 -m json.tool >/dev/null; }
healthy(){ [ "$(docker inspect -f '{{.State.Health.Status}}' "$1" 2>/dev/null)" = healthy ]; }
local_bind(){ docker port stremio-webadmin 8090/tcp | grep -q '^127.0.0.1:8090$'; }
direct_mode(){ docker inspect -f '{{.HostConfig.NetworkMode}}' stremio-libtorrent-server | grep -q '^stremioserverwebadmin_stremio-net$'; }
no_extra(){ [ "$(docker ps --format '{{.Names}}' | wc -l)" -eq 2 ]; }
dns_ok(){ docker exec stremio-libtorrent-server getent ahostsv4 github.com >/dev/null; }
check "Stremio healthy" healthy stremio-libtorrent-server
check "WebAdmin healthy" healthy stremio-webadmin
check "DIRECT Docker bridge" direct_mode
check "Only Stremio and WebAdmin containers" no_extra
check "WebAdmin on loopback" local_bind
check "Core health" http_ok "http://$source_host:11470/health"
check "Web player HTTP" http_ok "http://$source_host:8080/"
check "HTTPS Core" http_ok "https://$source_host:12470/health"
check "WebAdmin health" http_ok "http://127.0.0.1:8090/health"
check "WebAdmin HTTPS LAN" http_ok "https://$source_host/"
check "WebAdmin HTTPS API" json_ok "https://$source_host/api/status"
check "HTTPS transcoding API" json_ok "https://$source_host/api/transcoding/status"
check "Docker DNS resolution" dns_ok
echo "$pass DIRECT checks passed"
