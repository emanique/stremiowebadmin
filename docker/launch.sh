#!/bin/sh
# Durable launcher: always starts this fork's streaming runtime, using NVIDIA when available and
# degrading to Intel VAAPI / CPU otherwise. Safe to re-run (recreates the container).
#
# The `docker run --gpus all` flag hard-fails when the NVIDIA container runtime or driver is
# missing, so this script probes it before deciding which GPU flags to use.
#
# Env overrides: NAME, IMAGE, DATA, WEB_PORT, HTTP_PORT, HTTPS_PORT, BT_PORT,
#                STREMIOSRV_CACHE_SIZE, SERVER_URL and STREMIOSRV_LIBRARY_*.
set -e

NAME="${NAME:-stremio-libtorrent-server}"
IMAGE="${IMAGE:-stremio-webadmin:local}"
DATA="${DATA:-/root/stremio-data}"
WEB_PORT="${WEB_PORT:-8080}"
HTTP_PORT="${HTTP_PORT:-11470}"
HTTPS_PORT="${HTTPS_PORT:-12470}"
BT_PORT="${BT_PORT:-6881}"
CACHE_SIZE="${STREMIOSRV_CACHE_SIZE:-85899345920}"
LIBRARY_UI="${STREMIOSRV_LIBRARY_UI:-true}"
LIBRARY_ALLOW_HTTP="${STREMIOSRV_LIBRARY_ALLOW_HTTP:-false}"
LIBRARY_OWNER="${STREMIOSRV_LIBRARY_OWNER:-}"
LIBRARY_ADDON_ALLOW="${STREMIOSRV_LIBRARY_ADDON_ALLOW:-}"

ENV_ARGS=""
[ -n "${SERVER_URL:-}" ] && ENV_ARGS="-e SERVER_URL=$SERVER_URL"

# Auto-detect the host LAN IP so the container fetches a trusted *.stremio.rocks cert.
# Override with IPADDRESS=<ip>, or IPADDRESS="" to skip automatic certificate discovery.
IPADDRESS="${IPADDRESS-$(ip route get 1.1.1.1 2>/dev/null | grep -oE 'src [0-9.]+' | cut -d' ' -f2)}"
[ -n "${IPADDRESS}" ] && ENV_ARGS="$ENV_ARGS -e IPADDRESS=$IPADDRESS" && echo "[launch] IPADDRESS=$IPADDRESS -> trusted stremio.rocks cert"

GPU_ARGS=""

if docker run --rm --gpus all --entrypoint true "$IMAGE" >/dev/null 2>&1; then
    GPU_ARGS="--gpus all"
    echo "[launch] NVIDIA usable -> NVENC (--gpus all)"
else
    echo "[launch] NVIDIA not usable -> falling back to VAAPI/CPU"
fi

if [ -e /dev/dri ]; then
    GPU_ARGS="$GPU_ARGS --device /dev/dri:/dev/dri"
    echo "[launch] /dev/dri present -> VAAPI enabled"
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true

# shellcheck disable=SC2086 # GPU_ARGS/ENV_ARGS are intentionally word-split
docker run -d --name "$NAME" --restart unless-stopped $GPU_ARGS $ENV_ARGS \
  -e STREMIOSRV_BT_LISTEN_PORT="$BT_PORT" \
  -e STREMIOSRV_CACHE_ROOT=/root/.stremio-server \
  -e CERT_FILE=certificates.pem \
  -e STREMIOSRV_CACHE_SIZE="$CACHE_SIZE" \
  -e STREMIOSRV_LIBRARY_UI="$LIBRARY_UI" \
  -e STREMIOSRV_LIBRARY_ALLOW_HTTP="$LIBRARY_ALLOW_HTTP" \
  -e STREMIOSRV_LIBRARY_OWNER="$LIBRARY_OWNER" \
  -e STREMIOSRV_LIBRARY_ADDON_ALLOW="$LIBRARY_ADDON_ALLOW" \
  -v "$DATA":/root/.stremio-server \
  -p "$WEB_PORT":8080 -p "$HTTP_PORT":11470 -p "$HTTPS_PORT":12470 \
  -p "$BT_PORT":"$BT_PORT"/tcp -p "$BT_PORT":"$BT_PORT"/udp \
  --health-cmd "curl -fsS http://127.0.0.1:11470/health || exit 1" \
  --health-interval 30s --health-timeout 5s --health-retries 3 --health-start-period 15s \
  --label monitor.enabled=true --label monitor.name="$NAME" \
  --label monitor.health.path=/health --label monitor.health.port=11470 \
  --log-opt max-size=20m --log-opt max-file=5 \
  "$IMAGE"

echo "[launch] started $NAME from $IMAGE (gpu args: ${GPU_ARGS:-none})"
