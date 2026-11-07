#!/bin/sh
set -eu
export PATH="/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
ROOT="${STREMIO_INSTALL_ROOT:-/opt/stremio-server-webadmin}"
[ "$(id -u)" -eq 0 ] || { echo "Run as root" >&2; exit 1; }
test -f "$ROOT/packaging/stremio-webadmin-prerequisites.sh" || {
  echo "Missing bundled prerequisite installer" >&2; exit 1;
}
sh "$ROOT/packaging/stremio-webadmin-prerequisites.sh"
test -f "$ROOT/start.sh"
mkdir -p /var/lib/stremio-webadmin /etc/stremio-webadmin
chmod 700 /etc/stremio-webadmin
if [ ! -f "$ROOT/.env" ]; then (umask 077; cp "$ROOT/.env.example" "$ROOT/.env"); fi
chmod 600 "$ROOT/.env"
if [ ! -f /var/lib/stremio-webadmin/port-status.json ]; then
  printf '%s\n' '{"result":"unconfigured","detail":"Gateway host policy and dedicated token not provisioned"}' > /var/lib/stremio-webadmin/port-status.json
fi
python3 "$ROOT/scripts/validate-existing-deployment.py" "$ROOT/.env"
python3 "$ROOT/scripts/ensure-port-status.py"
python3 "$ROOT/scripts/sync-host-dns.py" "$ROOT/.env"
python3 "$ROOT/scripts/configure-host-https.py" --apply
cd "$ROOT"
sh ./start.sh config --quiet
sh ./start.sh
install -m 0644 "$ROOT/systemd/stremio-webadmin-reconcile.service" /etc/systemd/system/
install -m 0644 "$ROOT/systemd/stremio-webadmin-reconcile.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now stremio-webadmin-reconcile.timer
systemctl start stremio-webadmin-reconcile.service
echo "Stremio WebAdmin configured. Register the Gateway host policy/token for BT auto-sync."