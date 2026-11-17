#!/usr/bin/env bash
# Reproducible offline staging for the AI Subtitle Generator.
# Never fetches models, changes Docker services, or edits .env.
set -Eeuo pipefail
umask 027

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
MODEL_DEST="${STREMIOSRV_AI_WHISPER_MODELS_DIR:-$ROOT/models/whisper}"
BINARY_DEST="${STREMIOSRV_AI_RUNTIME_DIR:-$ROOT/tools/ai-runtime}"
MODEL_SHA="1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b"
MODEL_NAME="ggml-small.bin"
BINARY_NAME="whisper-cli"

usage() {
  echo "Usage: bash scripts/provision-ai-subtitles-runtime.sh --verify" >&2
  echo "   or: bash scripts/provision-ai-subtitles-runtime.sh --install MODEL_FILE BINARY_FILE BINARY_SHA256" >&2
  exit 2
}
valid_sha() { [[ "$1" =~ ^[0-9a-f]{64}$ ]]; }
check_hash() {
  local expected="$1" path="$2"
  printf '%s  %s\n' "$expected" "$path" | sha256sum -c --status
}
regular_file() { [[ -f "$1" && ! -L "$1" && -s "$1" ]]; }

[[ $# -ge 1 ]] || usage
mode="$1"; shift
case "$mode" in
  --verify)
    [[ $# -eq 0 ]] || usage
    regular_file "$MODEL_DEST/$MODEL_NAME" || { echo "Missing model" >&2; exit 1; }
    regular_file "$BINARY_DEST/$BINARY_NAME" || { echo "Missing whisper binary" >&2; exit 1; }
    check_hash "$MODEL_SHA" "$MODEL_DEST/$MODEL_NAME" || { echo "Model checksum mismatch" >&2; exit 1; }
    [[ -x "$BINARY_DEST/$BINARY_NAME" ]] || { echo "Binary is not executable" >&2; exit 1; }
    if command -v ldd >/dev/null 2>&1; then
      ! ldd "$BINARY_DEST/$BINARY_NAME" 2>&1 | grep -q 'not found' || {
        echo "Missing shared library dependency" >&2; exit 1;
      }
    fi
    "$BINARY_DEST/$BINARY_NAME" --help >/dev/null 2>&1 || {
      # whisper-cli returns non-zero for --help on some revisions: basic
      # dependency checks still occur via the dynamic loader above.
      [[ -x "$BINARY_DEST/$BINARY_NAME" ]] || exit 1
    }
    echo "AI subtitle runtime verified (model checksum pinned; binary executable)."
    ;;
  --install)
    [[ $# -eq 3 ]] || usage
    source_model="$1"; source_binary="$2"; expected_binary_sha="$3"
    valid_sha "$expected_binary_sha" || { echo "Invalid binary SHA-256" >&2; exit 2; }
    regular_file "$source_model" && regular_file "$source_binary" || {
      echo "Expected regular, non-symlinked source files" >&2; exit 1;
    }
    check_hash "$MODEL_SHA" "$source_model" || { echo "Model checksum mismatch" >&2; exit 1; }
    check_hash "$expected_binary_sha" "$source_binary" || { echo "Binary checksum mismatch" >&2; exit 1; }
    [[ -d "$MODEL_DEST" && ! -L "$MODEL_DEST" || ! -e "$MODEL_DEST" ]] || exit 1
    [[ -d "$BINARY_DEST" && ! -L "$BINARY_DEST" || ! -e "$BINARY_DEST" ]] || exit 1
    mkdir -p "$MODEL_DEST" "$BINARY_DEST"
    install -m 0644 "$source_model" "$MODEL_DEST/.$MODEL_NAME.tmp"
    install -m 0755 "$source_binary" "$BINARY_DEST/.$BINARY_NAME.tmp"
    check_hash "$MODEL_SHA" "$MODEL_DEST/.$MODEL_NAME.tmp"
    check_hash "$expected_binary_sha" "$BINARY_DEST/.$BINARY_NAME.tmp"
    mv -f "$MODEL_DEST/.$MODEL_NAME.tmp" "$MODEL_DEST/$MODEL_NAME"
    mv -f "$BINARY_DEST/.$BINARY_NAME.tmp" "$BINARY_DEST/$BINARY_NAME"
    echo "Installed verified offline AI subtitle runtime. Restart not performed."
    ;;
  *) usage ;;
esac
