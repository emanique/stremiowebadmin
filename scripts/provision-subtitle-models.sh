#!/usr/bin/env bash
# Provision the five pinned offline translation models for a clean installation.
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
  "translate-ru_en-1_9.argosmodel|e9ba8bf722d10a4a4c39f74289d5938fd47eac08dbe4ed0afd22d89445a5c3ac|ru_en|https://data.argosopentech.com/argospm/v1/translate-ru_en-1_9.argosmodel"
  "translate-cs_en-1_9_6.argosmodel|f1683b1ac5e826c7e3db2b0ea5d73191cdd695d6523b112363d8a769203dcb62|cs_en|https://data.argosopentech.com/argospm/v1/translate-cs_en-1_9_6.argosmodel"
)
for item in "${packages[@]}"; do
  IFS='|' read -r filename expected pair source <<< "$item"
  archive="$DOWNLOAD_DIR/$filename"
  installed="$DEST/$pair"
  if [[ -d "$installed" ]]; then
    python3 - "$installed/INSTALL.json" "$expected" "$pair" <<'PY'
import json, pathlib, sys
meta_path = pathlib.Path(sys.argv[1])
expected = sys.argv[2]; src, dst = sys.argv[3].split("_")
folder=meta_path.parent
for name in ("model/model.bin","model/config.json"):
    assert (folder / name).is_file(), name
assert ((folder / "sentencepiece.model").is_file() or
        (folder / "bpe.model").is_file()), "missing supported tokenizer"
if meta_path.is_file():
    meta=json.loads(meta_path.read_text())
    assert (meta.get("source"),meta.get("target"),meta.get("sha256")) == (src,dst,expected)
else:
    provenance=folder/"SOURCE_SHA256.txt"
    assert provenance.is_file() and provenance.read_text().split()[0] == expected, "missing verified model provenance"
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
echo "[model] all five offline model paths verified: en->pt, fr->en->pt, es->pt, ru->en->pt, cs->en->pt"
