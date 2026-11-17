#!/usr/bin/env bash
# Supply a downloaded Intel encoder archive; no automatic downloads or service restarts.
set -Eeuo pipefail
umask 027
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
DEST="${STREMIOSRV_AI_OPENVINO_MODELS_DIR:-$ROOT/models/openvino}"
CPU_MODEL="$ROOT/models/whisper/ggml-small.bin"
ZIP_SHA='7efabdcb08ec09ee24b615db9fe86425114a46d87a25d1e3d14ecd7195dd4e9f'
CPU_SHA='1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b'
ENC_SHA='dcf387e963f2af348224e033a84862ed56e800abcacab94f5ea2122a9fc1cb84'
XML_SHA='ae65701f18ed73c2a054fd115c0ac087a5884815e23e4de5da3210800898526f'
verify_file() { printf '%s  %s\n' "$1" "$2" | sha256sum -c --status; }
verify_file "$CPU_SHA" "$CPU_MODEL" || { echo 'CPU Whisper model checksum mismatch' >&2; exit 2; }
case "${1:---verify}" in
  --verify)
    verify_file "$ENC_SHA" "$DEST/ggml-small-encoder-openvino.bin" && verify_file "$XML_SHA" "$DEST/ggml-small-encoder-openvino.xml" || { echo 'OpenVINO encoder not provisioned' >&2; exit 3; }
    echo 'Verified OpenVINO Whisper Small encoder and matching ggml Small model.'
    ;;
  --install-archive)
    [[ $# -eq 2 && -f "$2" && ! -L "$2" ]] || exit 2
    verify_file "$ZIP_SHA" "$2" || { echo 'Archive checksum mismatch' >&2; exit 3; }
    mkdir -p "$DEST"
    python3 - "$2" "$DEST" <<'PY'
import sys, pathlib, tempfile, zipfile, os, hashlib
source, dest = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
known = {
    'ggml-small-encoder-openvino.bin': 'dcf387e963f2af348224e033a84862ed56e800abcacab94f5ea2122a9fc1cb84',
    'ggml-small-encoder-openvino.xml': 'ae65701f18ed73c2a054fd115c0ac087a5884815e23e4de5da3210800898526f',
}
with zipfile.ZipFile(source) as archive:
    for filename, digest in known.items():
        data = archive.read(filename)
        if hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit(f'Invalid encoder file: {filename}')
        fd, temp = tempfile.mkstemp(dir=dest, prefix='.openvino-stage-')
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o644)
        os.replace(temp, dest / filename)
print('OpenVINO encoder installed; no containers restarted.')
PY
    ;;
  *) echo 'Usage: provision-ai-openvino-runtime.sh --verify | --install-archive ZIP' >&2; exit 2 ;;
esac
