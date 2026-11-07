#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
if ! command -v docker >/dev/null; then echo "Docker missing" >&2; exit 1; fi
if ! docker compose version >/dev/null; then echo "Compose missing" >&2; exit 1; fi
if ! test -x .venv/bin/pytest; then
    echo "Create local environment with 'uv sync --frozen --python 3.12'" >&2
    exit 1
fi
export TMPDIR="${TMPDIR:-$ROOT/.test-tmp}"
mkdir -p "$TMPDIR"
if [ "$(df -Pk "$TMPDIR" | awk 'NR==2 {print $4}')" -lt 12582912 ]; then
    echo "QA temporary filesystem must have at least 12 GiB free" >&2
    exit 1
fi
export IPADDRESS="${IPADDRESS:-$(ip route get 1.1.1.1 | awk '{for (i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}')}"
docker compose config --quiet
sh -n start.sh
.venv/bin/pytest -q --tb=short "$@"
