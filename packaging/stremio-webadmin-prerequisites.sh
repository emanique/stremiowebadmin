#!/bin/sh
# Bootstrap only missing host packages on Debian/Ubuntu for a source clone.
# No Docker image build, VPN settings, data deletion, or network reconfiguration.
set -eu
export PATH="/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
[ "$(id -u)" -eq 0 ] || { echo "Run as root" >&2; exit 1; }

need=''
for pair in 'docker:docker.io' 'nginx:nginx' 'openssl:openssl' 'ip:iproute2' 'python3:python3'; do
    command=${pair%%:*}
    package=${pair#*:}
    if ! command -v "$command" >/dev/null 2>&1; then
        need="$need $package"
    fi
done
if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemd is required; no automatic replacement of the init system" >&2
    exit 1
fi
missing_compose=0
missing_buildx=0
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    missing_compose=1
fi
if ! command -v docker >/dev/null 2>&1 || ! docker buildx version >/dev/null 2>&1; then
    missing_buildx=1
fi
if [ -z "$need" ] && [ "$missing_compose" -eq 0 ] && [ "$missing_buildx" -eq 0 ]; then
    compose_version=$(docker compose version --short 2>/dev/null | sed 's/^v//')
    case "$compose_version" in
        2.*|3.*|4.*|5.*) echo "Host prerequisites already present (Compose v2 and Buildx)."; exit 0 ;;
        *) echo "Docker Compose v2+ required (found: $compose_version)." >&2; exit 1 ;;
    esac
fi
if [ "${STREMIO_SKIP_DEP_INSTALL:-0}" = '1' ]; then
    echo "Host prerequisites missing; automatic APT installation disabled." >&2
    exit 1
fi
if [ ! -r /etc/os-release ]; then
    echo "Unsupported host: /etc/os-release unavailable." >&2
    exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in
    debian|ubuntu) ;;
    *) echo "Automatic installation is supported only on Debian/Ubuntu." >&2; exit 1 ;;
esac
command -v apt-get >/dev/null 2>&1 || { echo "APT unavailable" >&2; exit 1; }
echo "Installing missing host prerequisites using APT..."
apt-get update
if [ "$missing_compose" -eq 1 ]; then
    # Ubuntu names its distro package docker-compose-v2; Debian 13
    # names the v2 package docker-compose. Official Docker uses the
    # docker-compose-plugin package. Reject classic v1 by CLI version check.
    for candidate in docker-compose-v2 docker-compose-plugin docker-compose; do
        if apt-cache show "$candidate" 2>/dev/null | grep -q '^Package: '; then
            need="$need $candidate"
            break
        fi
    done
    case " $need " in
        *' docker-compose-v2 '*|*' docker-compose-plugin '*|*' docker-compose '*) ;;
        *) echo "No supported Docker Compose package found in APT." >&2; exit 1 ;;
    esac
fi
if [ "$missing_buildx" -eq 1 ]; then
    for candidate in docker-buildx docker-buildx-plugin; do
        if apt-cache show "$candidate" 2>/dev/null | grep -q '^Package: '; then
            need="$need $candidate"
            break
        fi
    done
    case " $need " in
        *' docker-buildx '*|*' docker-buildx-plugin '*) ;;
        *) echo "No supported Docker Buildx package found in APT." >&2; exit 1 ;;
    esac
fi
# Install the missing set only. Do not overwrite an existing Docker engine.
# Word splitting is intentional because 'need' consists solely of constant package names.
# shellcheck disable=SC2086
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $need
if command -v docker >/dev/null 2>&1; then
    systemctl enable --now docker
fi
for binary in docker python3 nginx openssl ip systemctl; do
    command -v "$binary" >/dev/null 2>&1 || { echo "Missing prerequisite after APT: $binary" >&2; exit 1; }
done
docker compose version >/dev/null 2>&1 || {
    echo "Docker Compose v2 unavailable after installation; verify the package repository." >&2
    exit 1
}
compose_version=$(docker compose version --short 2>/dev/null | sed 's/^v//')
case "$compose_version" in
    2.*|3.*|4.*|5.*) ;;
    *) echo "Docker Compose v2+ required (found: $compose_version)." >&2; exit 1 ;;
esac
docker buildx version >/dev/null 2>&1 || {
    echo "Docker Buildx plugin unavailable after installation." >&2; exit 1
}
echo "Host prerequisites verified (Docker Compose v2 and Buildx)."
