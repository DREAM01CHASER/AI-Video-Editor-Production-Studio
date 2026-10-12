"""Local speech-to-text with faster-whisper.

Transcription runs entirely on this machine. The source video is decoded by
faster-whisper's own PyAV bridge, so no intermediate audio file is written and
FFmpeg is not required for this step.

Design notes:

* The model is loaded lazily and cached, so a retry does not pay the load cost
  again, and the default ``medium`` model is read from the normal Hugging Face
  cache rather than being committed to the repository.
* GPU is preferred when CTranslate2 can see a device, but every load is guarded
  by a CPU/int8 fallback because a CUDA device can be visible while the cuDNN or
  cuBLAS runtime is still missing. Any fallback is recorded in the transcript.
* Word timings are used when available and omitted when they are not, rather
  than reporting invented timings.
* Only one transcription runs at a time: the target machine has 6 GB of VRAM and
  the project rules say not to run heavy jobs concurrently by default.
"""

from __future__ import annotations

import importlib
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .config import Settings
from .errors import ApiError

TRANSCRIPT_VERSION = 1

#: Progress budget, so the bar reflects real phases instead of jumping to 100.
_LOAD_SHARE = 5.0
_SAVE_SHARE = 3.0

ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ pure logic


def cuda_device_count() -> int:
    """Return how many CUDA devices CTranslate2 can see, or 0 if unavailable."""

    try:
        ctranslate2 = importlib.import_module("ctranslate2")
        return int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001 - any failure simply means "no GPU path"
        return 0


def resolve_device(requested: str, available_devices: int) -> tuple[str, str | None]:
    """Pick the device to use, returning ``(device, note)``.

    ``auto`` prefers a visible CUDA device. An explicitly requested CUDA device
    that is not visible falls back to CPU, and the fallback is reported rather
    than hidden.
    """

    normalised = (requested or "auto").strip().lower()
    if normalised in {"cuda", "gpu"}:
        if available_devices > 0:
            return "cuda", None
        return "cpu", "CUDA was requested but no device is visible; using CPU instead."
    if normalised == "cpu":
        return "cpu", None
    if normalised == "auto":
        if available_devices > 0:
            return "cuda", None
        return "cpu", None
    raise ApiError(
        "invalid_whisper_device",
        f"WHISPER_DEVICE must be auto, cpu, or cuda (got '{requested}').",
        400,
    )


def resolve_compute_type(device: str, requested: str) -> str:
    """Pick a compute type appropriate to the device.

    ``auto`` means float16 on GPU and int8 on CPU: float16 halves VRAM use and
    int8 is the fastest reliable option without a GPU.
    """

    normalised = (requested or "auto").strip().lower()
    if normalised == "auto":
        return "int8" if device == "cpu" else "float16"
    if device == "cpu" and normalised.startswith("float16"):
        # float16 on CPU is either unsupported or extremely slow.
        return "int8"
    return normalised


def word_to_dict(word: object) -> dict[str, float | str]:
    """Convert one faster-whisper word token into a plain JSON object."""

    return {
        "start": round(float(getattr(word, "start", 0.0) or 0.0), 3),
        "end": round(float(getattr(word, "end", 0.0) or 0.0), 3),
        "word": str(getattr(word, "word", "") or "").strip(),
        "probability": round(float(getattr(word, "probability", 0.0) or 0.0), 4),
    }


def segment_to_dict(segment: object, index: int) -> dict[str, object]:
    """Convert one faster-whisper segment into a plain JSON object.

    Word timings are attached only when the model really produced them.
    """

    payload: dict[str, object] = {
        "id": f"seg-{index + 1}",
        "start": round(float(getattr(segment, "start", 0.0) or 0.0), 3),
        "end": round(float(getattr(segment, "end", 0.0) or 0.0), 3),
        "text": str(getattr(segment, "text", "") or "").strip(),
    }

    raw_words = getattr(segment, "words", None)
    if raw_words:
        words = [word_to_dict(word) for word in raw_words]
        if words:
            payload["words"] = words

    return payload


def build_transcript_document(
    *,
    project_id: str,
    source_video: str,
    segments: Sequence[object],
    info: object,
    model: str,
    device: str,
    compute_type: str,
    duration: float,
    elapsed_seconds: float,
    requested_word_timestamps: bool,
    notes: Iterable[str] = (),
) -> dict[str, object]:
    """Assemble the transcript JSON document that is saved to the project."""

    converted = [segment_to_dict(segment, index) for index, segment in enumerate(segments)]
    has_words = any("words" in segment for segment in converted)

    language = getattr(info, "language", None)
    probability = getattr(info, "language_probability", None)

    return {
        "version": TRANSCRIPT_VERSION,
        "project_id": project_id,
        "source_video": source_video,
        "model": model,
        "device": device,
        "compute_type": compute_type,
        "language": language,
        "language_probability": round(float(probability), 4) if probability else None,
        "duration": round(float(duration), 3),
        "segment_count": len(converted),
        "has_word_timestamps": has_words,
        "generated_at": _now(),
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "notes": list(notes),
        "segments": converted,
    }


def transcript_summary(document: dict) -> dict[str, object]:
    """The compact reference stored inside ``project.json``."""

    return {
        "path": "transcripts/transcript.json",
        "model": document.get("model"),
        "language": document.get("language"),
        "segment_count": document.get("segment_count", 0),
        "has_word_timestamps": document.get("has_word_timestamps", False),
        "generated_at": document.get("generated_at"),
    }


# ------------------------------------------------------------------- service


@dataclass
class _LoadedModel:
    instance: object
    device: str
    compute_type: str
    notes: list[str] = field(default_factory=list)


class _CudaRuntimeMissing(RuntimeError):
    """Raised when CTranslate2 fails because a CUDA runtime DLL is unavailable."""


#: Substrings that identify a missing CUDA runtime rather than a real bug. CTranslate2
#: loads a CUDA model successfully and only fails on the first inference, so these
#: have to be recognised at inference time as well as at load time.
_CUDA_RUNTIME_ERRORS = (
    "cublas",
    "cudnn",
    "cudart",
    "cufft",
    "cannot load library",
    "d3d11",
)


def _is_cuda_runtime_failure(exc: BaseException) -> bool:
    """Whether an exception is a missing CUDA runtime rather than anything else."""

    if isinstance(exc, _CudaRuntimeMissing):
        return True
    text = str(exc).lower()
    if not any(marker in text for marker in _CUDA_RUNTIME_ERRORS):
        return False
    # Only treat it as a runtime problem when a CUDA device was actually in use.
    return "cuda" in text or "cublas" in text or "cudnn" in text


class TranscriptionService:
    """Owns the faster-whisper model and turns a video into a transcript."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model: _LoadedModel | None = None
        self._model_lock = threading.Lock()
        # Serialise transcription so two jobs cannot share one model at once.
        self._run_lock = threading.Lock()

    # ------------------------------------------------------------ model load

    def _load_module(self) -> object:
        try:
            return importlib.import_module("faster_whisper")
        except ImportError as exc:
            raise ApiError(
                "transcriber_unavailable",
                "faster-whisper is not installed. Run: "
                "python -m pip install -r requirements.txt",
                503,
            ) from exc

    def _build_model(self, device: str, compute_type: str) -> object:
        module = self._load_module()
        try:
            return module.WhisperModel(
                self.settings.whisper_model,
                device=device,
                compute_type=compute_type,
                cpu_threads=self.settings.whisper_cpu_threads,
                download_root=(
                    str(self.settings.model_cache_dir)
                    if self.settings.model_cache_dir
                    else None
                ),
                local_files_only=not self.settings.whisper_allow_model_download,
            )
        except Exception as exc:  # noqa: BLE001 - re-raised with a clear message
            raise ApiError(
                "model_unavailable",
                self._model_error_message(exc),
                status_code=503,
            ) from exc

    def _model_error_message(self, exc: Exception) -> str:
        name = type(exc).__name__
        model = self.settings.whisper_model

        if isinstance(exc, ImportError):
            return (
                "faster-whisper is missing a runtime dependency. "
                "Run: python -m pip install -r requirements.txt"
            )

        lowered = str(exc).lower()
        if "cudnn" in lowered or "cublas" in lowered:
            return (
                "The GPU runtime libraries (cuDNN/cuBLAS) are missing, so the CUDA "
                "device could not be used. Set WHISPER_DEVICE=cpu to run on CPU."
            )
        if "no such file" in lowered or "not found" in lowered or "local_files_only" in lowered:
            return (
                f"The Whisper model '{model}' is not in the local model cache and "
                "downloading is disabled (WHISPER_ALLOW_DOWNLOAD=false). Allow the "
                "download for the first transcription, or point WHISPER_MODEL at a "
                "converted model directory."
            )
        return (
            f"Could not load the Whisper model '{model}' ({name}). The first "
            "transcription downloads model weights, which needs an internet "
            "connection; after that the model is reused from the local cache. "
            f"Details: {exc}"
        )

    def load_model(self, progress: ProgressCallback | None = None) -> _LoadedModel:
        """Return the cached model, loading it on first use.

        A CUDA load failure transparently retries on CPU with int8 and records
        the fallback, so a missing cuDNN does not break transcription.
        """

        with self._model_lock:
            if self._model is not None:
                return self._model

            if progress:
                progress(0.0, self._loading_message())

            device, note = resolve_device(
                self.settings.whisper_device, cuda_device_count()
            )
            compute_type = resolve_compute_type(device, self.settings.whisper_compute_type)
            notes = [note] if note else []

            try:
                instance = self._build_model(device, compute_type)
            except ApiError:
                if device != "cuda":
                    raise
                # cuDNN or cuBLAS is usually the real reason a GPU load fails.
                fallback_type = resolve_compute_type("cpu", self.settings.whisper_compute_type)
                try:
                    instance = self._build_model("cpu", fallback_type)
                except ApiError:
                    raise
                notes.append(
                    "The GPU model load failed, so transcription ran on CPU/int8."
                )
                device, compute_type = "cpu", fallback_type

            self._model = _LoadedModel(
                instance=instance, device=device, compute_type=compute_type, notes=notes
            )
            return self._model

    def _loading_message(self) -> str:
        if self.settings.whisper_allow_model_download:
            return (
                f"Loading Whisper '{self.settings.whisper_model}' — the first "
                "transcription may download model weights."
            )
        return f"Loading Whisper '{self.settings.whisper_model}' from the local cache."

    # ----------------------------------------------------------- transcription

    def transcribe(
        self,
        *,
        source_path: Path,
        project_id: str,
        source_video: str,
        duration_hint: float = 0.0,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> tuple[dict[str, object], _LoadedModel]:
        """Transcribe a local video and return ``(document, loaded_model)``.

        Progress is reported per decoded segment, so the value always reflects
        audio that has actually been transcribed.
        """

        def report(value: float, message: str) -> None:
            if progress:
                progress(value, message)

        def cancelled() -> bool:
            return bool(should_cancel and should_cancel())

        waiting = self._run_lock.locked()
        if waiting:
            report(0.0, "Waiting for the current transcription to finish…")

        started = time.time()
        with self._run_lock:
            if cancelled():
                raise ApiError("job_cancelled", "Transcription was cancelled.", 409)

            loaded = self.load_model(progress)
            notes = list(loaded.notes)
            if waiting:
                notes.append("This job waited for another transcription to finish.")

            report(_LOAD_SHARE, "Model ready. Decoding audio…")

            module = self._load_module()

            def start_inference(instance) -> tuple:
                """Begin a transcription, converting a missing CUDA runtime.

                ``faster-whisper`` decodes and detects the language inside this
                call, before the segment iterator is ever handed back, so this is
                where a missing cuBLAS first appears on most machines.
                """

                try:
                    return instance.transcribe(
                        str(source_path),
                        word_timestamps=self.settings.whisper_word_timestamps,
                        beam_size=self.settings.whisper_beam_size,
                        language=self.settings.whisper_language,
                        vad_filter=True,
                    )
                except BaseException as exc:
                    if loaded.device == "cuda" and _is_cuda_runtime_failure(exc):
                        raise _CudaRuntimeMissing(str(exc)) from exc
                    raise

            try:
                segments_iter, info = start_inference(loaded.instance)
                # The first segment is pulled here so a GPU runtime failure is
                # caught while it can still be retried. CTranslate2 loads a CUDA
                # model successfully even when the cuBLAS runtime is missing, and
                # only fails on the first real inference, so a load-time fallback
                # alone is not enough.
                collected, info, device, compute_type = self._collect_segments(
                    segments_iter,
                    info=info,
                    loaded=loaded,
                    duration_hint=duration_hint,
                    cancelled=cancelled,
                    report=report,
                )
            except _CudaRuntimeMissing:
                if loaded.device != "cuda":
                    raise
                notes.append(
                    "The GPU runtime libraries (cuDNN/cuBLAS) are missing, so "
                    "transcription ran on CPU/int8 instead."
                )
                report(_LOAD_SHARE, "The GPU runtime is unavailable — retrying on CPU…")
                loaded = self._fallback_to_cpu()
                segments_iter, info = start_inference(loaded.instance)
                collected, info, device, compute_type = self._collect_segments(
                    segments_iter,
                    info=info,
                    loaded=loaded,
                    duration_hint=duration_hint,
                    cancelled=cancelled,
                    report=report,
                )

            total = float(getattr(info, "duration", 0.0) or 0.0) or float(duration_hint or 0.0)

            if not collected:
                notes.append(
                    "No speech was detected in this file. Check that the video has an "
                    "audible audio track."
                )

            elapsed = time.time() - started
            document = build_transcript_document(
                project_id=project_id,
                source_video=source_video,
                segments=collected,
                info=info,
                model=self.settings.whisper_model,
                device=device,
                compute_type=compute_type,
                duration=total,
                elapsed_seconds=elapsed,
                requested_word_timestamps=self.settings.whisper_word_timestamps,
                notes=notes,
            )

            if not document["has_word_timestamps"] and self.settings.whisper_word_timestamps:
                document["notes"].append(
                    "Word-level timings were not available for this audio; "
                    "segment timings are used instead."
                )

            report(97.0, "Saving transcript…")
            return document, loaded

    def _fallback_to_cpu(self) -> _LoadedModel:
        """Rebuild the model on CPU/int8 and remember it for later jobs."""

        compute_type = resolve_compute_type("cpu", self.settings.whisper_compute_type)
        instance = self._build_model("cpu", compute_type)
        loaded = _LoadedModel(
            instance=instance, device="cpu", compute_type=compute_type, notes=[]
        )
        with self._model_lock:
            self._model = loaded
        return loaded

    def _collect_segments(
        self,
        segments_iter,
        *,
        info,
        loaded: _LoadedModel,
        duration_hint: float,
        cancelled,
        report,
    ) -> tuple[list[object], object, str, str]:
        """Drain the segment iterator, reporting progress from real decoded audio.

        The very first segment is pulled inside the caller's ``try`` so that a
        missing CUDA runtime — which only surfaces on the first inference — can
        still be retried on the CPU instead of failing the whole job.
        """

        total = float(getattr(info, "duration", 0.0) or 0.0) or float(duration_hint or 0.0)
        span = 100.0 - _LOAD_SHARE - _SAVE_SHARE

        collected: list[object] = []
        try:
            for segment in segments_iter:
                if cancelled():
                    raise ApiError("job_cancelled", "Transcription was cancelled.", 409)
                collected.append(segment)
                end = float(getattr(segment, "end", 0.0) or 0.0)
                fraction = (end / total) if total > 0 else 0.0
                report(
                    _LOAD_SHARE + span * min(1.0, max(0.0, fraction)),
                    f"Transcribed {end:.0f}s of {total:.0f}s — {len(collected)} segments.",
                )
        except ApiError:
            raise
        except BaseException as exc:
            # Anything else may still be a missing CUDA runtime; recognise it here
            # so the caller can retry on the CPU. `raise ... from exc` keeps the
            # original for the log.
            if loaded.device == "cuda" and _is_cuda_runtime_failure(exc):
                raise _CudaRuntimeMissing(str(exc)) from exc
            raise

        return collected, info, loaded.device, loaded.compute_type

    @property
    def is_busy(self) -> bool:
        return self._run_lock.locked()
