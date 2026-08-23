"""Background jobs with status polling.

BUILD.md: "Long runs are jobs with status polling, never blocking requests."

A reconciliation over 25,000 rows takes seconds for the deterministic layers and minutes
if the LLM is involved. An HTTP request that waits for that is a request that times out at
a proxy nobody controls -- and on a hosted platform the proxy is not ours. So every run is
submitted, gets an id, and is polled.

WHY AN IN-PROCESS REGISTRY AND NOT CELERY

The alternative is a broker, a worker container, and a second thing that can be down during
a demo. The job load here is one run at a time, started by one person clicking a button.
A thread and a dict is the correct size for that, and it has no failure mode that is not
also the API's failure mode.

The honest limitation, stated rather than discovered: **jobs do not survive a restart.**
That is acceptable because the demo's seeded run is read from the repository rather than
from job state, so a cold start has something to show without having run anything.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Bounded, so a wedged demo cannot accumulate threads without limit.
MAX_JOBS_RETAINED = 50

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


@dataclass
class Job:
    job_id: str
    kind: str
    status: str = STATUS_QUEUED
    # 0.0 to 1.0 where the work can report it, None where it genuinely cannot. A fake
    # progress bar that always reaches 90% and stops is worse than an honest spinner.
    progress: float | None = None
    step: str = ""
    result: Any = None
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str = ""

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "step": self.step,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            # The result is deliberately excluded: it can be large, and polling should
            # stay cheap. Fetch it from the run endpoints once status is done.
            "has_result": self.result is not None,
        }


class JobRegistry:
    """Thread-safe. One lock, held only around dict access, never during the work."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def submit(self, kind: str, work: Callable[[Job], Any]) -> Job:
        job = Job(job_id=uuid.uuid4().hex[:12], kind=kind)

        with self._lock:
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            while len(self._order) > MAX_JOBS_RETAINED:
                self._jobs.pop(self._order.pop(0), None)

        def run() -> None:
            job.status = STATUS_RUNNING
            try:
                job.result = work(job)
                job.status = STATUS_DONE
                job.progress = 1.0
            except Exception as exc:
                job.status = STATUS_FAILED
                # The message, plus the last frame. A demo that fails should say what
                # failed; a stack trace in a browser is not that, and no trace at all
                # leaves nobody able to help.
                job.error = f"{type(exc).__name__}: {exc}"[:400]
                job.step = traceback.format_exc().strip().splitlines()[-1][:200]
            finally:
                job.finished_at = datetime.now(UTC).isoformat()

        threading.Thread(target=run, name=f"job-{job.job_id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        with self._lock:
            return [self._jobs[i] for i in reversed(self._order[-limit:]) if i in self._jobs]


REGISTRY = JobRegistry()
