#!/usr/bin/env bash
# Provision exactly the three locally validated CPU subtitle model packages.
# Model binaries, provider credentials and individual subtitles never enter Git.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${STREMIOSRV_SUBTITLE_MODELS_DIR:-$ROOT/models/subtitles}"
DOWNLOAD_DIR="${STREMIOSRV_SUBTITLE_DOWNLOAD_DIR:-$ROOT/models/downloads}"
MODE="${1:---verify}"
[[ "$MODE" == "--verify" || "$MODE" == "--install" || "$MODE" == "--download-and-install" ]] || {
  echo "Usage: $0 [--verify|--install|--download-and-install]" >&2; exit 2;
}
mkdir -p "$DOWNLOAD_DIR" "$DEST"
cd "$ROOT"
# Pinned model digests tested on DEV .250 2026-11-12.
declare -a packages=(
  "translate-en_pt-1_9.argosmodel|0c5350a2fa5b923de1346edc0d42e08e38bab2e33cede3a0b9a48eb4281ad8a9|en_pt|https://data.argosopentech.com/argospm/v1/translate-en_pt-1_9.argosmodel"
  "translate-fr_en-1_9.argosmodel|3b3052fee6bb1e8e8e632a26a723eb2a2c7710dfe73ba61ffd9b83e85d4f14c1|fr_en|https://data.argosopentech.com/argospm/v1/translate-fr_en-1_9.argosmodel"
  "es_pt.argosmodel|c772951dee3917f799b701bde0612331f54e1aa09d709dd057bee6b41bb04936|es_pt|https://argos-net.com/v1/translate-es_pt-1_0.argosmodel"
)
for item in "${packages[@]}"; do
  IFS='|' read -r filename expected pair source <<< "$item"
  archive="$DOWNLOAD_DIR/$filename"
  installed="$DEST/$pair"
  if [[ -d "$installed" ]]; then
    python3 - "$installed/INSTALL.json" "$expected" "$pair" <<'PY'
import json, pathlib, sys
meta = json.loads(pathlib.Path(sys.argv[1]).read_text())
expected = sys.argv[2]; src, dst = sys.argv[3].split("_")
assert (meta.get("source"),meta.get("target"),meta.get("sha256")) == (src,dst,expected)
folder=pathlib.Path(sys.argv[1]).parent
for name in ("model/model.bin","model/config.json","sentencepiece.model"):
    assert (folder / name).is_file(), name
PY
    echo "[model] verified installed $pair"
    continue
  fi
  if [[ "$MODE" == "--download-and-install" ]]; then
    command -v curl >/dev/null || { echo "curl is required" >&2; exit 3; }
    curl -fLS --retry 3 --connect-timeout 15 --max-time 600 \
      "$source" -o "$archive.tmp"
    mv -f "$archive.tmp" "$archive"
  fi
  [[ -f "$archive" ]] || { echo "[model] missing archive for $pair: $archive" >&2; exit 4; }
  printf '%s  %s\n' "$expected" "$archive" | sha256sum -c -
  if [[ "$MODE" == "--verify" ]]; then
    echo "[model] verified package $pair; not installed"
  else
    python3 "$ROOT/scripts/subtitle-model-install.py" \
      --package "$archive" --destination "$DEST" --sha256 "$expected"
  fi
done
echo "[model] all three offline model paths verified: en->pt, fr->en->pt, es->pt"
