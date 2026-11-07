#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
VERSION="$(cat RELEASE_VERSION)"
DEB_VERSION="${VERSION/-rc./~rc}"
OUT="${1:-$PWD/dist}"
mkdir -p "$OUT"
OUT="$(cd "$OUT"; pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/source"
git archive HEAD | tar -xf - -C "$WORK/source"
test -f "$WORK/source/RELEASE_VERSION"
test "$(cat "$WORK/source/RELEASE_VERSION")" = "$VERSION"
EPOCH="$(git show -s --format=%ct HEAD)"
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="@$EPOCH" -C "$WORK" -czf "$OUT/stremio-webadmin-${VERSION}-source.tar.gz" source
mkdir -p "$WORK/deb/DEBIAN" "$WORK/deb/opt/stremio-server-webadmin" "$WORK/deb/usr/local/sbin"
cp -a "$WORK/source/." "$WORK/deb/opt/stremio-server-webadmin/"
install -m 0755 "$WORK/source/packaging/stremio-webadmin-setup.sh" "$WORK/deb/usr/local/sbin/stremio-webadmin-setup"
cat > "$WORK/deb/DEBIAN/control" <<DEB_CONTROL
Package: stremio-webadmin
Version: $DEB_VERSION
Section: net
Priority: optional
Architecture: all
Maintainer: StremioServerWebAdmin DEV <dev-stremioserverwebadmin@users.noreply.github.com>
Depends: docker.io | docker-ce, docker-compose-v2 | docker-compose-plugin | docker-compose, nginx, python3, iproute2, openssl, ca-certificates
Description: Standalone Stremio Core and WebAdmin source-build package
 Docker Compose application, hardware transcoding and host reconcile tooling.
 This package carries sources; it does not contain published GHCR images.
 Install, then run sudo stremio-webadmin-setup on the target host.
DEB_CONTROL
cat > "$WORK/deb/DEBIAN/postinst" <<'DEB_POSTINST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
  if [ ! -f /opt/stremio-server-webadmin/.env ]; then
    (umask 077; cp /opt/stremio-server-webadmin/.env.example /opt/stremio-server-webadmin/.env)
  fi
  chmod 600 /opt/stremio-server-webadmin/.env
  mkdir -p /var/lib/stremio-webadmin
  echo "Run: sudo stremio-webadmin-setup"
fi
DEB_POSTINST
chmod 0755 "$WORK/deb/DEBIAN/postinst"
dpkg-deb --root-owner-group --build "$WORK/deb" "$OUT/stremio-webadmin_${DEB_VERSION}_all.deb" >/dev/null
(
 cd "$OUT"
 sha256sum "stremio-webadmin-${VERSION}-source.tar.gz" "stremio-webadmin_${DEB_VERSION}_all.deb" > SHA256SUMS
)
printf 'Prepared QA v%s from %s\n' "$VERSION" "$(git rev-parse --short HEAD)"
ls -lh "$OUT"
