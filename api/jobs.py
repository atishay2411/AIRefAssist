"""In-memory job store for bulk reference processing.

A batch of N references takes ~15–20s each; processing it inside one HTTP
request dies at any proxy timeout. Jobs decouple submission from retrieval:

    POST   /api/jobs            → {job_id}   (starts background processing)
    GET    /api/jobs/{id}       → status + progress (+ results when done)
    GET    /api/jobs/{id}/report→ ZIP built from STORED results (no re-run)
    DELETE /api/jobs/{id}       → cancel

Scope note: the store is per-process. Run the API single-worker (default),
or put a shared store (e.g. Redis) behind this interface before scaling to
multiple workers — the sweep/limit logic is the only thing to swap.
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:  # package-style run: `uvicorn api.app:app` from the repo root
    from api.uploads import env_pos_int
except ImportError:  # direct run from inside api/
    from uploads import env_pos_int

JOB_TTL_S = env_pos_int("REFASSIST_JOB_TTL", 3600, minimum=60)
MAX_ACTIVE_JOBS = env_pos_int("REFASSIST_MAX_ACTIVE_JOBS", 3)
MAX_STORED_JOBS = env_pos_int("REFASSIST_MAX_STORED_JOBS", 100)

_ACTIVE = ("queued", "running")
_FINISHED = ("completed", "failed", "cancelled")


@dataclass
class Job:
    id: str
    refs: List[str]
    status: str = "queued"
    total: int = 0
    done: int = 0
    results: List[dict] = field(default_factory=list)
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    task: Optional[asyncio.Task] = None

    @property
    def finished(self) -> bool:
        return self.status in _FINISHED


class JobStore:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}

    def _sweep(self) -> None:
        now = time.time()
        expired = [
            jid for jid, j in self._jobs.items()
            if j.finished and (now - (j.finished_at or j.created_at)) > JOB_TTL_S
        ]
        for jid in expired:
            del self._jobs[jid]
        # Hard cap on stored jobs: evict the oldest finished ones first.
        if len(self._jobs) > MAX_STORED_JOBS:
            finished = sorted(
                (j for j in self._jobs.values() if j.finished),
                key=lambda j: j.finished_at or j.created_at,
            )
            for j in finished[: len(self._jobs) - MAX_STORED_JOBS]:
                del self._jobs[j.id]

    def active_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status in _ACTIVE)

    def create(self, refs: List[str]) -> Job:
        self._sweep()
        if self.active_count() >= MAX_ACTIVE_JOBS:
            raise RuntimeError(
                f"Server is already processing {MAX_ACTIVE_JOBS} batches; retry shortly."
            )
        job = Job(id=uuid.uuid4().hex[:16], refs=refs, total=len(refs))
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        self._sweep()
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Optional[Job]:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.status in _ACTIVE and job.task is not None:
            job.task.cancel()
        return job


STORE = JobStore()
