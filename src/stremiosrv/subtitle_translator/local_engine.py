"""CPU-first CTranslate2 backend for independently provisioned Argos-format models.

Models are installed separately under STREMIOSRV_SUBTITLE_MODELS_ROOT.
Expected layout: <root>/<source>_<target>/model/{model.bin,config.json}
and <root>/<source>_<target>/sentencepiece.model or bpe.model.

No model download or installation occurs on a playback request.
No Torch, Stanza or full Argos Python runtime is required.
"""
from __future__ import annotations

import os
import re
import threading
from functools import lru_cache
from pathlib import Path

from stremiosrv.subtitle_translator.hardware import (
    BackendUnavailable, backend_policy,
)

_MODEL_PAIR = re.compile(r"^[a-z]{2}_[a-z]{2}$")
DEFAULT_ROOT = "/opt/stremio-subtitle-models"
_LOAD_LOCK = threading.RLock()


class LocalModelUnavailable(RuntimeError):
    pass


def model_root() -> Path:
    return Path(os.getenv("STREMIOSRV_SUBTITLE_MODELS_ROOT", DEFAULT_ROOT))


class BpeMosesTokenizer:
    """Argos-compatible BPE tokenizer with language-aware Moses normalisation."""

    def __init__(self, base: Path, source: str, target: str):
        from sacremoses import MosesDetokenizer, MosesPunctNormalizer, MosesTokenizer
        from subword_nmt.apply_bpe import BPE

        self.normalizer = MosesPunctNormalizer(lang=source)
        self.tokenizer = MosesTokenizer(lang=source)
        self.detokenizer = MosesDetokenizer(lang=target)
        with (base / "bpe.model").open("r", encoding="utf-8") as handle:
            self.bpe = BPE(handle)

    def encode(self, text: str, out_type=str):
        normalized = self.normalizer.normalize(text)
        words = self.tokenizer.tokenize(normalized)
        return self.bpe.segment_tokens(words)

    def decode(self, tokens):
        return self.detokenizer.detokenize(" ".join(tokens).replace("@@ ", "").split(" "))


def _tokenizer_file(base: Path) -> Path | None:
    for filename in ("sentencepiece.model", "bpe.model"):
        candidate = base / filename
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def installed_pairs(root: Path | None = None) -> set[tuple[str, str]]:
    root = root or model_root()
    if not root.is_dir():
        return set()
    found: set[tuple[str, str]] = set()
    for entry in root.iterdir():
        if not _MODEL_PAIR.fullmatch(entry.name) or not entry.is_dir() or entry.is_symlink():
            continue
        if ((entry / "model" / "model.bin").is_file()
                and (entry / "model" / "config.json").is_file()
                and _tokenizer_file(entry) is not None):
            source, target = entry.name.split("_", 1)
            found.add((source, target))
    return found


@lru_cache(maxsize=8)
def _load_pair(source: str, target: str, location: str,
               device: str = "cpu"):
    try:
        import ctranslate2
    except ImportError as exc:
        raise LocalModelUnavailable("ctranslate2/sentencepiece not installed") from exc
    pair = f"{source}_{target}"
    if not _MODEL_PAIR.fullmatch(pair):
        raise LocalModelUnavailable("unsupported model language pair")
    base = Path(location) / pair
    if (source, target) not in installed_pairs(Path(location)):
        raise LocalModelUnavailable(f"model not installed: {pair}")
    with _LOAD_LOCK:
        if (base / "sentencepiece.model").is_file():
            import sentencepiece
            sp = sentencepiece.SentencePieceProcessor(model_file=str(base / "sentencepiece.model"))
        elif (base / "bpe.model").is_file():
            sp = BpeMosesTokenizer(base, source, target)
        else:
            raise LocalModelUnavailable(f"missing tokenizer: {pair}")
        translator = ctranslate2.Translator(
            str(base / "model"),
            device=device,
            device_index=0,
            compute_type="auto",
            inter_threads=1,
            intra_threads=max(1, min(4, int(os.getenv("STREMIOSRV_SUBTITLE_CPU_THREADS", "2")))),
        )
        return sp, translator


def _select_model(source: str, target: str, location: str):
    """Instantiate the requested accelerator; auto may fall back on a load error."""
    policy = backend_policy()
    try:
        return _load_pair(source, target, location, policy.selected)
    except (RuntimeError, OSError) as exc:
        if policy.requested != "auto" or policy.selected != "cuda":
            raise
        # CUDA capability probes alone do not guarantee successful model load,
        # particularly when GPU memory is shared with video transcoding.
        return _load_pair(source, target, location, "cpu")


def translation_path(source: str, target: str = "pt",
                     *, root: Path | None = None) -> tuple[tuple[str, str], ...]:
    """Return only installed offline steps; prefer a direct model to a pivot.

    French can use fr->en->pt when no direct fr->pt model is installed.
    No external translation endpoint and no implicit online model download.
    """
    src = source.lower().split("-", 1)[0]
    dst = target.lower().split("-", 1)[0]
    installed = installed_pairs(root)
    if src == dst:
        return ()
    if (src, dst) in installed:
        return ((src, dst),)
    if (src, "en") in installed and ("en", dst) in installed:
        return ((src, "en"), ("en", dst))
    return ()


def supported_source_languages(*, root: Path | None = None) -> set[str]:
    """Languages with a complete, installed path to Portuguese."""
    return {source for source, _ in installed_pairs(root)
            if source != "pt" and translation_path(source, "pt", root=root)}


def translate_many(
    texts: list[str], source: str, target: str = "pt-PT",
    *, root: Path | None = None, batch_size: int = 32,
) -> list[str]:
    """Local-only direct or pivot translation with stable cue order."""
    if not texts:
        return []
    src = source.lower().split("-", 1)[0]
    dst = target.lower().split("-", 1)[0]
    location = str(root or model_root())
    path = translation_path(src, dst, root=Path(location))
    if not path:
        raise LocalModelUnavailable(f"offline translation path unavailable: {src}->{dst}")

    def _run(current_sp, current_translator, original_texts: list[str]) -> list[str]:
        output: list[str] = []
        for start in range(0, len(original_texts), batch_size):
            original = original_texts[start:start + batch_size]
            segmented = [current_sp.encode(t, out_type=str) for t in original]
            translated = current_translator.translate_batch(
                segmented, beam_size=4, max_decoding_length=256,
            )
            # Some recent Argos CTranslate2 packages emit literal SentencePiece
            # word-boundary markers in decoded hypotheses. Convert only
            # that model artefact; leave normal punctuation and spacing intact.
            output.extend(
                current_sp.decode(row.hypotheses[0]).replace("▁", " ").strip()
                for row in translated
            )
        if len(output) != len(original_texts):
            raise RuntimeError("local translation returned incorrect cue count")
        return output

    values = texts
    for step_src, step_dst in path:
        sp, translator = _select_model(step_src, step_dst, location)
        try:
            values = _run(sp, translator, values)
        except (RuntimeError, OSError):
            if (backend_policy().requested != "auto"
                    or str(getattr(translator, "device", "cpu")) != "cuda"):
                raise
            cpu_sp, cpu_translator = _load_pair(step_src, step_dst, location, "cpu")
            values = _run(cpu_sp, cpu_translator, values)
    return values
