"""Priority-ordered, single-worker offline AI subtitles (disabled by default).

The worker keeps only safe, status-only metadata in memory. A Core restart does
not delete generated subtitles; the queue is rebuilt from verified torrents.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import threading
import time

from stremiosrv.ai_subtitles.generator import GenerationUnavailable, cache_key, generate
from stremiosrv.ai_subtitles.priority import local_languages, scheduling_priority, translatable_languages
from stremiosrv.ai_subtitles.resume_scan import complete_cached_files

logger = logging.getLogger(__name__)
VIDEO = frozenset({".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v", ".ts"})


class AutoSubtitleWorker:
    def __init__(self, engine, cache_root: str, interval: float = 60):
        self.engine = engine
        self.root = Path(cache_root).resolve()
        self.interval = max(10, float(interval))
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.failed_until: dict[str, float] = {}
        self._lock = threading.RLock()
        self._jobs: list[dict] = []
        self._active: dict | None = None
        self._last_scan = 0.0

    def start(self):
        if os.getenv("STREMIOSRV_AI_SUBTITLES_ENABLED", "0").lower() not in ("1", "true", "yes"):
            return
        if self.engine is None:
            return
        model = Path(os.environ.get("STREMIOSRV_AI_WHISPER_MODEL", ""))
        if not model.is_file() or model.is_symlink():
            logger.warning("AI subtitles disabled: local Whisper model missing")
            return
        self.thread = threading.Thread(target=self._run, name="ai-subtitle-generator", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def snapshot(self) -> dict:
        """An instant nonblocking queue snapshot; no disk scan on HTTP requests."""
        with self._lock:
            rows = [dict(job) for job in self._jobs]
            active = dict(self._active) if self._active else None
            if active:
                active["elapsedSeconds"] = max(0, round(time.monotonic() - active["_started"]))
                active.pop("_started", None)
            return {
                "enabled": os.getenv("STREMIOSRV_AI_SUBTITLES_ENABLED", "0").lower()
                           in ("1", "true", "yes"),
                "active": active,
                "jobs": rows[:40],
                "priorityRule": "Subtitle Translator first for usable PT/EN/other caption; Whisper only when no translatable subtitle is available",
                "coverage": "Only verified local subtitles are considered; a Translator selection may be required and remote addons are not probed",
                "lastScanAgeSeconds": max(0, round(time.monotonic() - self._last_scan)) if self._last_scan else None,
            }

    def _set_stage(self, stage: str) -> None:
        with self._lock:
            if self._active:
                self._active["status"] = stage
                if stage == "transcribing":
                    self._active["progressPercent"] = 0
                elif stage != "transcribing":
                    self._active.pop("progressPercent", None)

    def _set_progress(self, percent: int) -> None:
        """Record actual whisper.cpp inference progress, never an ETA."""
        with self._lock:
            if self._active and self._active.get("status") == "transcribing":
                self._active["progressPercent"] = max(
                    int(self._active.get("progressPercent", 0)), min(99, max(0, int(percent)))
                )

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.scan_once()
            except Exception:
                logger.exception("AI subtitle scan failed")
            self.stop_event.wait(self.interval)

    def scan_once(self):
        """Queue all complete video files, then take highest priority first."""
        model = Path(os.environ.get("STREMIOSRV_AI_WHISPER_MODEL", ""))
        if not model.is_file() or model.is_symlink() or self.engine is None:
            return False
        candidates: list[tuple[int, dict, Path, int, str, str]] = []
        jobs: list[dict] = []
        now = time.monotonic()
        active = list(self.engine.active())
        active_hashes: set[str] = set()
        sources: list[tuple[str, int, Path, int, str]] = []
        for handle in active:
            try:
                ih = handle.info_hash().lower()
                active_hashes.add(ih)
                if not handle.has_metadata():
                    continue
                count = handle.num_files()
                for index, raw in enumerate(handle.file_paths()):
                    relative = Path(raw)
                    if (relative.is_absolute() or ".." in relative.parts
                            or relative.suffix.lower() not in VIDEO):
                        continue
                    media = (self.root / relative).resolve()
                    if not media.is_relative_to(self.root) or not media.is_file() or media.is_symlink():
                        continue
                    if handle.file_complete(index):
                        sources.append((ih, index, media, count, "live"))
            except (OSError, RuntimeError, ValueError):
                logger.exception("AI subtitles: invalid active torrent handle")

        # Saved resume records let complete torrents outlive their active
        # session handles. They are trusted only after independent checks of
        # torrent hash, verified pieces, file size and disk allocation.
        for ih, index, media, count in complete_cached_files(self.root, active_hashes):
            sources.append((ih, index, media, count, "cached"))

        for ih, index, media, count, source in sources:
            try:
                key = cache_key(ih, index, 0, model.name)
                target = self.root / ".ai-subtitles" / ih / (key + ".srt")
                lang = local_languages(media, self.root, ih,
                                       file_index=index, file_count=count)
                priority, reason = scheduling_priority(lang, translatable=translatable_languages(lang))
                job = {
                    "infohash": ih, "fileIndex": index,
                    "title": media.name[:180], "status": "queued",
                    "priority": priority, "reason": reason,
                    "localLanguages": sorted(lang), "source": source,
                }
                if target.is_file() and not target.is_symlink() and target.stat().st_size:
                    job["status"] = "ready"
                    self.failed_until.pop(key, None)
                elif priority == 2:
                    job["status"] = "existing_portuguese"
                elif priority == 1:
                    job["status"] = "subtitle_translator_first"
                elif self.failed_until.get(key, 0) > now:
                    job["status"] = "retry_later"
                else:
                    candidates.append((priority, job, media, index, ih, key))
                jobs.append(job)
            except (OSError, RuntimeError, ValueError):
                logger.exception("AI subtitles: unable to inspect cached media")
        # Stable FIFO within the same language priority (engine discovery order).
        candidates.sort(key=lambda item: item[0])
        with self._lock:
            self._jobs = jobs
            self._last_scan = time.monotonic()
        if not candidates or self.stop_event.is_set():
            return False

        _, job, media, index, ih, key = candidates[0]
        with self._lock:
            job["status"] = "extracting_audio"
            self._active = {**job, "_started": time.monotonic()}
        try:
            generate(media, self.root, ih, index, 0, verified_complete=True,
                     model_path=model, on_stage=self._set_stage,
                     on_progress=self._set_progress)
            with self._lock:
                job["status"] = "ready"
                if self._active:
                    self._active["status"] = "ready"
            logger.info("AI pt-PT subtitle created for %s file=%d", ih[:8], index)
        except GenerationUnavailable as exc:
            self.failed_until[key] = time.monotonic() + 6 * 3600
            with self._lock:
                job["status"] = "retry_later"
                job["error"] = str(exc)[:160]
                if self._active:
                    self._active["status"] = "retry_later"
                    self._active["error"] = str(exc)[:160]
            logger.warning("AI subtitle generation failed for %s: %s", ih[:8], exc)
        finally:
            with self._lock:
                self._active = None
        return True
