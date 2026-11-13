#!/usr/bin/env python3
"""Install an inspected Argos model archive into the CPU subtitle runtime.

Offline by design: takes a .argosmodel file already downloaded by an operator,
validates its hash and metadata, and extracts ONLY required inference artefacts.
Never modifies existing installed models or Core Python dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

SHA256 = re.compile(r"^[0-9a-f]{64}$")
PAIR = re.compile(r"^[a-z]{2}$")
MAX_ARCHIVE_SIZE = 250 * 1024 * 1024
MAX_MODEL_UNCOMPRESSED = 500 * 1024 * 1024

def install(archive: Path, destination: Path, sha256: str) -> Path:
    if not SHA256.fullmatch(sha256):
        raise ValueError("required SHA-256 must be 64 hex characters")
    if not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE_SIZE:
        raise ValueError("model archive missing or oversized")
    checksum = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
    if checksum != sha256:
        raise ValueError("model SHA-256 mismatch")
    with zipfile.ZipFile(archive) as package:
        items = package.infolist()
        if len(items) > 200 or sum(x.file_size for x in items) > MAX_MODEL_UNCOMPRESSED:
            raise ValueError("model package exceeds extraction limits")
        roots = {x.filename.split("/", 1)[0] for x in items}
        if len(roots) != 1:
            raise ValueError("archive must contain exactly one model root")
        root = roots.pop()
        prefix = root + "/"
        members = {
            "metadata.json", "sentencepiece.model",
            "model/model.bin", "model/config.json",
            "model/shared_vocabulary.json",
            "model/source_vocabulary.json",
            "model/target_vocabulary.json",
        }
        names = {x.filename for x in items}
        if prefix + "metadata.json" not in names:
            raise ValueError("archive metadata missing")
        meta = json.loads(package.read(prefix + "metadata.json"))
        source = meta.get("from_code")
        target = meta.get("to_code")
        if not (isinstance(source, str) and PAIR.fullmatch(source)
                and isinstance(target, str) and PAIR.fullmatch(target)):
            raise ValueError("invalid model language pair")
        required = {"sentencepiece.model", "model/model.bin", "model/config.json"}
        if any(prefix + name not in names for name in required):
            raise ValueError("incomplete inference model")
        if any(x.filename.startswith("/") or ".." in Path(x.filename).parts for x in items):
            raise ValueError("unsafe model archive paths")
        selected = [x for x in items if x.filename.removeprefix(prefix) in members]
        if any(((x.external_attr >> 16) & 0o170000) == stat.S_IFLNK for x in selected):
            raise ValueError("symlinks are forbidden in model archive")
        destination.mkdir(parents=True, exist_ok=True)
        output = destination / f"{source}_{target}"
        if output.exists():
            raise FileExistsError(f"model already installed: {output}")
        temp = Path(tempfile.mkdtemp(prefix=".subtitle-model-", dir=destination))
        try:
            for item in selected:
                rel = item.filename.removeprefix(prefix)
                target_path = temp / rel
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as src, target_path.open("wb") as out:
                    shutil.copyfileobj(src, out, length=1024 * 1024)
            (temp / "INSTALL.json").write_text(json.dumps({
                "source": source, "target": target,
                "sha256": sha256, "original": archive.name,
            }, indent=2), encoding="utf-8")
            os.replace(temp, output)
            return output
        finally:
            if temp.exists():
                shutil.rmtree(temp)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    print(install(args.package, args.destination, args.sha256))


if __name__ == "__main__":
    main()
