#!/bin/sh
# Save direct-only configuration and volumes without external helper images.
# Stop the two Compose services before snapshotting the volumes.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
STAMP=$(date '+%Y%m%d-%H%M%S')
DEST=${BACKUP_DIR:-"$ROOT/backups/pre-upgrade-$STAMP"}
WITH_CACHE=false
[ "${1:-}" = "--with-cache" ] && WITH_CACHE=true
mkdir -p "$DEST/files" "$DEST/volumes"
if [ -f .env ]; then
    IPADDRESS=$(sed -n 's/^IPADDRESS=//p' .env | tail -1)
    export IPADDRESS
fi
for f in .env compose.yaml compose.gpu.yaml compose.vaapi.yaml; do
    [ ! -f "$f" ] || cp -p "$f" "$DEST/files/"
done
docker compose config > "$DEST/compose.resolved.yaml"
docker compose images > "$DEST/compose-images.txt" 2>&1 || true
docker compose ps > "$DEST/compose-ps.txt" 2>&1 || true
for container in stremio-libtorrent-server stremio-webadmin; do
    if [ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)" = true ]; then
        echo "[backup] Stop the two services before a consistent volume snapshot." >&2
        echo "[backup] Use: sh start.sh stop" >&2
        exit 1
    fi
done
backup_volume() {
    logical=$1
    actual="stremioserverwebadmin_${logical}"
    mountpoint=$(docker volume inspect -f '{{.Mountpoint}}' "$actual" 2>/dev/null || true)
    if [ -z "$mountpoint" ]; then
        echo "[backup] skip missing $actual"
        return
    fi
    echo "[backup] saving $actual"
    tar -czf "$DEST/volumes/${logical}.tar.gz" -C "$mountpoint" .
}
backup_volume stremio-config
backup_volume webadmin-data
if [ "$WITH_CACHE" = true ]; then
    backup_volume stremio-cache
fi
cat > "$DEST/RESTORE-NOTES.txt" <<EOF
Direct-only StremioServerWebAdmin backup: $STAMP
Source volumes use prefix stremioserverwebadmin_.
Critical: stremio-config and webadmin-data.
Optional: stremio-cache (included only with --with-cache).
Restore only while the stack is stopped; verify target volume names.
EOF
echo "[backup] saved to $DEST"
