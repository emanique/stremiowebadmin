"""Offline audio-to-pt-PT subtitle generation for verified complete torrent files.

Staging module: callers MUST verify libtorrent file_complete(file_index) before
calling generate(). No network activity, no background scheduling or player
hook is installed by this module alone.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading

HASH = re.compile(r"^[a-f0-9]{40}$")
LANG = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2})?$")
MODEL = re.compile(r"^[a-zA-Z0-9_.-]+$")
MAX_SUBTITLE = 4 * 1024 * 1024


def media_fingerprint(media: Path) -> str:
    """Small independent content guard against fast same-size file replacement.

    Sampling both ends avoids rereading multi-GB films. Together with inode,
    device, size, mtime and ctime this detects ordinary file replacement;
    it is not a cryptographic proof of the entire media content.
    """
    h = hashlib.sha256()
    with Path(media).open("rb") as stream:
        first = stream.read(65536)
        h.update(first)
        size = stream.seek(0, os.SEEK_END)
        if size > 65536:
            stream.seek(max(65536, size - 65536))
            h.update(stream.read(65536))
    return h.hexdigest()


class GenerationUnavailable(RuntimeError):
    pass


def cache_key(infohash: str, file_index: int, audio_index: int, model: str) -> str:
    if not HASH.fullmatch(infohash) or type(file_index) is not int or file_index < 0:
        raise ValueError("invalid torrent file identity")
    if type(audio_index) is not int or audio_index < 0 or not MODEL.fullmatch(model):
        raise ValueError("invalid audio/model identity")
    identity = ["ai-subtitles-v1", infohash, file_index, audio_index, model, "pt-PT"]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def _stamp(ms: int) -> str:
    ms = max(0, ms)
    h, remainder = divmod(ms, 3600000)
    m, remainder = divmod(remainder, 60000)
    s, millis = divmod(remainder, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


def json_to_srt(result: dict, *, allow_silence: bool = False) -> tuple[str, str]:
    """Convert whisper.cpp JSON; discard malformed segments, never invent timings."""
    transcription = result.get("transcription")
    language = str((result.get("result") or {}).get("language", "")).lower()
    language = {"english": "en", "french": "fr", "spanish": "es",
                "portuguese": "pt", "german": "de", "italian": "it",
                "russian": "ru", "chinese": "zh", "japanese": "ja",
                "korean": "ko", "dutch": "nl", "polish": "pl",
                "czech": "cs", "arabic": "ar"}.get(language, language)
    if not LANG.fullmatch(language):
        raise GenerationUnavailable("whisper output lacks a recognized language")
    if not isinstance(transcription, list):
        raise GenerationUnavailable("whisper output has no transcription")
    blocks = []
    last_end = 0
    for segment in transcription:
        if not isinstance(segment, dict):
            continue
        offsets = segment.get("offsets") or {}
        begin, end = offsets.get("from"), offsets.get("to")
        text = str(segment.get("text") or "").strip()
        if (type(begin) is not int or type(end) is not int or begin < 0
                or end <= begin or begin < last_end or not text or "-->" in text):
            continue
        blocks.append(f"{len(blocks)+1}\n{_stamp(begin)} --> {_stamp(end)}\n{text}")
        last_end = end
    # A successful Whisper response with zero segments can indicate genuine
    # silence. Only on-demand blocks opt in; malformed/non-empty responses
    # still fail so provider defects are not hidden.
    if allow_silence and transcription == []:
        return "", language
    output = "\n\n".join(blocks) + "\n"
    if not blocks or len(output.encode()) > MAX_SUBTITLE:
        raise GenerationUnavailable("transcription empty or too large")
    return output, language


# whisper.cpp's -pp flag prints "whisper_print_progress_callback: progress = N%".
# The callback is an actual fraction of inference completed, not a rate ETA.
_WHISPER_PROGRESS = re.compile(r"\bprogress\s*=\s*(\d{1,3})\s*%", re.IGNORECASE)


def run_whisper_with_progress(command: list[str], callback) -> None:
    """Stream progress from whisper.cpp without accumulating gigabytes of logs.

    A separate reader thread is needed so a stalled encoder cannot defeat the
    4-hour subprocess timeout by blocking on stderr.readline().
    """
    with subprocess.Popen(command + ["-pp"], stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          text=True, errors="replace", bufsize=1) as process:
        last = -1

        def consume():
            nonlocal last
            assert process.stderr is not None
            for line in process.stderr:
                match = _WHISPER_PROGRESS.search(line)
                if match:
                    value = min(99, int(match.group(1)))
                    if value > last:
                        last = value
                        callback(value)

        reader = threading.Thread(target=consume, name="whisper-progress-reader", daemon=True)
        reader.start()
        try:
            returncode = process.wait(timeout=4 * 3600)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        finally:
            reader.join(timeout=5)
        if returncode != 0:
            raise subprocess.CalledProcessError(returncode, command)



def run_openvino_bridge(job_dir: Path, endpoint: str, token: str, on_progress=None,
                        *, language: str = "auto") -> None:
    """GPU inference over internal Docker network, using shared scratch volume.

    All file operations stay in the caller's private temporary directory.
    The backend cannot access media or modify the torrent cache.
    """
    import urllib.request
    import urllib.error
    import time
    url = endpoint.rstrip("/")
    if not url.startswith("http://ai-openvino:19098"):
        raise GenerationUnavailable("OpenVINO bridge endpoint must be the internal service")
    if not token or len(token) < 32:
        raise GenerationUnavailable("OpenVINO bridge credentials missing")
    if not re.fullmatch(r"(?:auto|[a-z]{2,3})", language):
        raise GenerationUnavailable("invalid Whisper input language")
    finished = threading.Event()

    def poll():
        if on_progress is None:
            return
        while not finished.wait(2.0):
            try:
                req = urllib.request.Request(
                    url + "/status/" + job_dir.name, headers={"X-AI-Token": token})
                with urllib.request.urlopen(req, timeout=4) as response:
                    value = json.load(response).get("percent")
                if type(value) is int:
                    on_progress(min(99, max(0, value)))
            except (OSError, ValueError, KeyError):
                pass

    probe = threading.Thread(target=poll, name="openvino-progress-poll", daemon=True)
    if on_progress:
        probe.start()
    try:
        data = json.dumps({"job": job_dir.name, "language": language}).encode()
        req = urllib.request.Request(url + "/run", data=data,
                                     headers={"Content-Type": "application/json",
                                              "X-AI-Token": token}, method="POST")
        with urllib.request.urlopen(req, timeout=4 * 3600 + 90) as response:
            outcome = json.load(response)
        if not outcome.get("ok"):
            raise GenerationUnavailable("OpenVINO backend did not complete")
        if not (job_dir / "whisper.json").is_file():
            raise GenerationUnavailable("OpenVINO output is missing")
    except urllib.error.HTTPError as exc:
        # The bridge returns a controlled JSON error message. Preserve its
        # bounded diagnostic rather than losing the cause behind "HTTP 500".
        # Never expose URLs, auth headers or shared-work paths in logs.
        try:
            body = json.loads(exc.read(1024))
            reason = str(body.get("error", "")).strip()
        except (OSError, ValueError, TypeError, AttributeError):
            reason = ""
        # Whitelist printable text and cap length; error is internal-only.
        reason = "".join(ch for ch in reason if ch.isprintable())[:150]
        detail = f"OpenVINO HTTP {exc.code}"
        if reason:
            detail += ": " + reason
        raise GenerationUnavailable(detail) from exc
    except (OSError, ValueError, TimeoutError) as exc:
        raise GenerationUnavailable("OpenVINO inference failed: " + str(exc)[:140]) from exc
    finally:
        finished.set()
        if on_progress:
            probe.join(timeout=3)



def detect_speech_language(media: Path, bridge: str, token: str, work_root: str,
                           *, audio_index: int = 0, ffmpeg_bin: str = "ffmpeg") -> str:
    """Identify language from dialogue rather than a potentially silent intro.

    The 12-minute offset is a sampling heuristic, not a claim about content.
    A language without installed pt-PT translation is rejected *before*
    expensive whole-film transcription.
    """
    from stremiosrv.subtitle_translator.local_engine import translation_path
    import math
    sample_second = 720
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
            check=True, timeout=20, capture_output=True, text=True)
        duration = float(result.stdout.strip())
        if math.isfinite(duration) and duration > 0:
            sample_second = int(min(720, max(0, duration * 0.25)))
    except (OSError, ValueError, subprocess.SubprocessError):
        sample_second = 90
    with tempfile.TemporaryDirectory(prefix="ai-subtitles-probe", dir=work_root) as probe_dir:
        probe = Path(probe_dir)
        subprocess.run([ffmpeg_bin, "-nostdin", "-v", "error", "-ss", str(sample_second),
                        "-i", str(media), "-t", "35", "-map", f"0:a:{audio_index}", "-vn",
                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                        str(probe / "audio.wav")],
                       check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, timeout=120)
        run_openvino_bridge(probe, bridge, token)
        payload = json.loads((probe / "whisper.json").read_text("utf-8"))
        _, language = json_to_srt(payload)
    if language not in ("pt", "pt-pt") and not translation_path(language, "pt"):
        raise GenerationUnavailable(
            f"audio language {language} lacks a verified offline pt-PT translation model"
        )
    return language


def _raw_transcription(cache_root: Path, infohash: str, key: str,
                       original_stat, fingerprint: str):
    path = cache_root / ".ai-subtitles" / infohash / ("whisper-raw-" + key + ".json")
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SUBTITLE + 4096:
            return None
        data = json.loads(path.read_text("utf-8"))
        identity = [original_stat.st_dev, original_stat.st_ino, original_stat.st_size,
                    original_stat.st_mtime_ns, original_stat.st_ctime_ns, fingerprint]
        if (data.get("version") == 1 and data.get("identity") == identity
                and isinstance(data.get("srt"), str)
                and 0 < len(data["srt"].encode()) <= MAX_SUBTITLE
                and LANG.fullmatch(str(data.get("language", "")))):
            return data
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


def _persist_raw(cache_root: Path, infohash: str, key: str, media: Path,
                 original_stat, fingerprint: str, srt: str, language: str) -> None:
    """Keep a verified raw transcript if Argos translation fails afterward."""
    from stremiosrv import cache as cachemod
    path = cache_root / ".ai-subtitles" / infohash / ("whisper-raw-" + key + ".json")
    identity = [original_stat.st_dev, original_stat.st_ino, original_stat.st_size,
                original_stat.st_mtime_ns, original_stat.st_ctime_ns, fingerprint]
    with cachemod._TITLE_PURGE_LOCK:
        stat = media.stat()
        actual = [stat.st_dev, stat.st_ino, stat.st_size,
                  stat.st_mtime_ns, stat.st_ctime_ns, media_fingerprint(media)]
        if media.is_symlink() or identity != actual:
            raise GenerationUnavailable("media changed before raw transcript publication")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, pending = tempfile.mkstemp(prefix=".raw-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "identity": identity,
                           "srt": srt, "language": language}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, path)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)

def generate(media: Path, cache_root: Path, infohash: str, file_index: int,
             audio_index: int = 0, *, verified_complete: bool = False,
             model_path: Path | None = None, whisper_bin: str | None = None,
             ffmpeg_bin: str = "ffmpeg", on_stage=None, on_progress=None) -> Path:
    """Generate once; calling code must supply verified libtorrent completion.

    Intended to run in a bounded single-worker queue (not the HTTP event loop).
    Returns a stable SRT path; deleting the per-infohash folder clears output.
    """
    if not verified_complete:
        raise GenerationUnavailable("libtorrent completion has not been verified")
    media = Path(media)
    if not media.is_file() or media.is_symlink() or not media.stat().st_size:
        raise GenerationUnavailable("media missing or unsafe")
    original_stat = media.stat()
    original_fingerprint = media_fingerprint(media)
    model_path = Path(model_path or os.environ.get("STREMIOSRV_AI_WHISPER_MODEL", ""))
    if not model_path.is_file() or model_path.is_symlink():
        raise GenerationUnavailable("offline Whisper model is not installed")
    whisper_bin = whisper_bin or os.environ.get("STREMIOSRV_AI_WHISPER_BIN", "whisper-cli")
    threads = max(1, min(8, int(os.environ.get("STREMIOSRV_AI_WHISPER_THREADS", "2"))))
    name = model_path.name
    key = cache_key(infohash, file_index, audio_index, name)
    target_dir = Path(cache_root) / ".ai-subtitles" / infohash
    target = target_dir / (key + ".srt")
    if target.is_file() and not target.is_symlink() and target.stat().st_size:
        return target
    # Reserve room for a temporary 16 kHz mono PCM stream (~115 MiB per hour).
    # The source stays untouched; fail if a long movie might fill the filesystem.
    if shutil.disk_usage(tempfile.gettempdir()).free < 1024 * 1024 * 1024:
        raise GenerationUnavailable("insufficient temporary storage for audio extraction")
    # Audio temp files must never persist after failure or cancellation.
    bridge = os.environ.get("STREMIOSRV_AI_OPENVINO_URL", "").strip()
    work_dir = os.environ.get("STREMIOSRV_AI_OPENVINO_WORK_DIR", "").strip()
    if bridge and (not work_dir or not Path(work_dir).is_dir()):
        raise GenerationUnavailable("OpenVINO shared scratch volume unavailable")
    with tempfile.TemporaryDirectory(prefix="ai-subtitles-",
                                     dir=(work_dir if bridge else None)) as temp:
        temp_dir = Path(temp)
        wav = temp_dir / "audio.wav"
        output = temp_dir / "whisper"
        raw = _raw_transcription(Path(cache_root), infohash, key,
                                 original_stat, original_fingerprint) if bridge else None
        if raw is not None:
            srt, language = raw["srt"], raw["language"]
        else:
            token = os.environ.get("STREMIOSRV_AI_OPENVINO_TOKEN", "")
            language_hint = "auto"
            if bridge:
                if on_stage: on_stage("detecting_language")
                language_hint = detect_speech_language(
                    media, bridge, token, work_dir,
                    audio_index=audio_index, ffmpeg_bin=ffmpeg_bin)
            extract = [
                ffmpeg_bin, "-nostdin", "-v", "error",
                "-protocol_whitelist", "file,crypto,data", "-i", str(media),
                "-map", f"0:a:{audio_index}", "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(wav),
            ]
            try:
                if on_stage: on_stage("extracting_audio")
                subprocess.run(extract, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               check=True, timeout=4 * 3600)
                if on_stage: on_stage("transcribing")
                command = [whisper_bin, "-m", str(model_path), "-f", str(wav),
                           "-oj", "-of", str(output), "-l", language_hint,
                           "-t", str(threads)]
                if bridge:
                    run_openvino_bridge(temp_dir, bridge, token, on_progress,
                                        language=language_hint)
                elif on_progress is not None:
                    run_whisper_with_progress(command, on_progress)
                else:
                    subprocess.run(command, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, check=True, timeout=4 * 3600)
                result = json.loads(output.with_suffix(".json").read_text("utf-8"))
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                raise GenerationUnavailable("local audio transcription failed") from exc
            srt, language = json_to_srt(result)
            if bridge:
                _persist_raw(Path(cache_root), infohash, key, media, original_stat,
                             original_fingerprint, srt, language)
        if language not in ("pt", "pt-pt"):
            if on_stage: on_stage("translating")
            from stremiosrv.subtitle_translator import translate_srt_batch
            from stremiosrv.subtitle_translator.local_engine import translate_many
            try:
                srt = translate_srt_batch(srt, lambda texts: translate_many(texts, language, "pt-PT"))
            except (RuntimeError, ValueError, ImportError) as exc:
                raise GenerationUnavailable(
                    f"offline translation {language}->pt-PT failed: "
                    f"{type(exc).__name__}: {str(exc)[:110]}"
                ) from exc
        # Synchronise publication with cache.purge_title() and eviction. The
        # purge path holds the same RLock while removing torrent artefacts and
        # generated subtitles. If deletion won the race, never recreate SRT.
        if on_stage: on_stage("publishing")
        from stremiosrv import cache as cachemod
        with cachemod._TITLE_PURGE_LOCK:
            try:
                current_stat = media.stat()
            except OSError as exc:
                raise GenerationUnavailable("media removed during transcription") from exc
            if (media.is_symlink() or current_stat.st_ino != original_stat.st_ino
                    or current_stat.st_dev != original_stat.st_dev
                    or current_stat.st_size != original_stat.st_size
                    or current_stat.st_mtime_ns != original_stat.st_mtime_ns
                    or current_stat.st_ctime_ns != original_stat.st_ctime_ns
                    or media_fingerprint(media) != original_fingerprint):
                raise GenerationUnavailable("media changed during transcription")
            # The directory may have been removed by a purge while inference
            # was running. Recreate it only while the verified media still exists.
            target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, pending = tempfile.mkstemp(prefix=".srt-", dir=target_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(srt)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(pending, target)
                if bridge:
                    raw_path = target_dir / ("whisper-raw-" + key + ".json")
                    if raw_path.is_file() and not raw_path.is_symlink():
                        raw_path.unlink()
            finally:
                if os.path.exists(pending):
                    os.unlink(pending)
    return target


def delete_hash(cache_root: Path, infohash: str) -> int:
    """Explicit cleanup hook, to be called only after the hash cache is removed."""
    if not HASH.fullmatch(infohash):
        raise ValueError("invalid infohash")
    directory = Path(cache_root) / ".ai-subtitles" / infohash
    if not directory.is_dir() or directory.is_symlink():
        return 0
    count = 0
    for child in directory.iterdir():
        if child.name == "on-demand" and child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
            count += 1
            continue
        if child.is_file() and not child.is_symlink() and (
            child.suffix == ".srt" or
            (child.name.startswith("whisper-raw-") and child.suffix == ".json")
        ):
            child.unlink()
            count += 1
    if not any(directory.iterdir()):
        directory.rmdir()
    return count
