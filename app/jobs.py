"""In-process job registry for long-running local work.

Transcription can take minutes on CPU, so the HTTP request only starts the work
and returns a job id. The job itself runs on a plain ``threading.Thread``: the
project rules explicitly rule out Celery/Redis or any other queue framework for
a single-user local app.

Progress is reported by the work itself rather than animated, so the number the
UI shows always corresponds to segments that have really been transcribed.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .errors import ApiError

PENDING = "pending"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({SUCCEEDED, FAILED, CANCELLED})

#: Only the most recent jobs are kept so a long session cannot grow without bound.
MAX_RETAINED_JOBS = 50


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    """A single unit of background work and its observable state."""

    id: str
    kind: str
    #: Identifier of the thing being worked on (a project id), so routes can
    #: refuse to start the same heavy job twice on one project.
    subject: str | None = None
    status: str = PENDING
    progress: float = 0.0
    message: str = ""
    error: dict | None = None
    result: dict | None = None
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    cancel_requested: bool = False

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def as_dict(self) -> dict[str, object]:
        return {
            "job_id": self.id,
            "kind": self.kind,
            "subject": self.subject,
            "status": self.status,
            "progress": round(self.progress, 2),
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "cancel_requested": self.cancel_requested,
        }


class JobRegistry:
    """Thread-safe registry that runs one function per job on its own thread."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()

    # -------------------------------------------------------------- lifecycle

    def create(self, kind: str, subject: str | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex, kind=kind, subject=subject)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._trim()
        return job

    def running_for(self, kind: str, subject: str) -> Job | None:
        """Return an unfinished job of ``kind`` already working on ``subject``."""

        with self._lock:
            for job in self._jobs.values():
                if job.kind == kind and job.subject == subject and not job.finished:
                    return job
        return None

    def get(self, job_id: str) -> Job:
        """Return a job, or raise a structured 404."""

        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ApiError("job_not_found", f"No job exists with id '{job_id}'.", 404)
        return job

    def _trim(self) -> None:
        """Drop the oldest finished jobs once the retention cap is exceeded."""

        while len(self._order) > MAX_RETAINED_JOBS:
            for index, candidate_id in enumerate(self._order):
                candidate = self._jobs.get(candidate_id)
                if candidate is not None and candidate.finished:
                    self._order.pop(index)
                    self._jobs.pop(candidate_id, None)
                    break
            else:
                # Everything still running; stop trimming rather than losing work.
                return

    # ---------------------------------------------------------------- updates

    def _touch(self, job: Job) -> None:
        job.updated_at = _utcnow()

    def update(
        self,
        job: Job,
        *,
        progress: float | None = None,
        message: str | None = None,
    ) -> Job:
        """Record real progress from the running work."""

        with self._lock:
            if progress is not None:
                job.progress = min(100.0, max(0.0, progress))
            if message is not None:
                job.message = message
            self._touch(job)
        return job

    def succeed(self, job: Job, result: dict | None = None) -> Job:
        with self._lock:
            job.status = SUCCEEDED
            job.progress = 100.0
            job.result = result
            job.error = None
            self._touch(job)
        return job

    def fail(self, job: Job, code: str, message: str) -> Job:
        with self._lock:
            job.status = FAILED
            job.error = {"code": code, "message": message}
            job.message = message
            self._touch(job)
        return job

    def request_cancel(self, job_id: str) -> Job:
        """Ask a job to stop. Running work checks this flag as it makes progress."""

        job = self.get(job_id)
        with self._lock:
            if job.finished:
                return job
            job.cancel_requested = True
            job.message = "Cancelling…"
            self._touch(job)
        return job

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.cancel_requested)

    # ------------------------------------------------------------------- run

    def start(
        self,
        job: Job,
        target: Callable[..., dict | None],
        *args: object,
        **kwargs: object,
    ) -> Job:
        """Run ``target`` on a daemon thread and mirror its outcome onto the job."""

        def runner() -> None:
            with self._lock:
                job.status = RUNNING
                self._touch(job)
            try:
                result = target(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - reported through the job
                with self._lock:
                    cancelled = job.cancel_requested
                if cancelled or isinstance(exc, ApiError) and exc.code == "job_cancelled":
                    with self._lock:
                        job.status = CANCELLED
                        job.error = None
                        job.message = "Cancelled."
                        self._touch(job)
                elif isinstance(exc, ApiError):
                    self.fail(job, exc.code, exc.message)
                elif isinstance(exc, Exception):
                    self.fail(job, "job_failed", f"{type(exc).__name__}: {exc}")
                else:
                    self.fail(job, "job_failed", f"Unexpected {type(exc).__name__}")
            else:
                with self._lock:
                    cancelled = job.cancel_requested
                if cancelled:
                    with self._lock:
                        job.status = CANCELLED
                        job.message = "Cancelled before finishing."
                        self._touch(job)
                else:
                    self.succeed(job, result)

        thread = threading.Thread(
            target=runner, name=f"job-{job.kind}-{job.id[:8]}", daemon=True
        )
        thread.start()
        return job
