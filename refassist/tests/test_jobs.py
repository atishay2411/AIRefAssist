"""Unit tests for the bulk-processing job store (api/jobs.py). No network."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import jobs as jobs_mod  # noqa: E402
from api.jobs import Job, JobStore  # noqa: E402


def test_create_and_get():
    store = JobStore()
    job = store.create(["ref one", "ref two"])
    assert job.total == 2 and job.status == "queued"
    assert store.get(job.id) is job
    assert store.get("nope") is None


def test_active_job_limit(monkeypatch):
    monkeypatch.setattr(jobs_mod, "MAX_ACTIVE_JOBS", 2)
    store = JobStore()
    store.create(["a"])
    store.create(["b"])
    with pytest.raises(RuntimeError):
        store.create(["c"])
    # Finishing a job frees a slot
    for j in list(store._jobs.values())[:1]:
        j.status = "completed"
        j.finished_at = time.time()
    store.create(["c"])  # must not raise


def test_ttl_sweep(monkeypatch):
    monkeypatch.setattr(jobs_mod, "JOB_TTL_S", 60)
    store = JobStore()
    old = store.create(["a"])
    old.status = "completed"
    old.finished_at = time.time() - 3600  # long expired
    active = store.create(["b"])          # sweep runs on create
    assert store.get(old.id) is None
    assert store.get(active.id) is active


def test_running_jobs_never_swept(monkeypatch):
    monkeypatch.setattr(jobs_mod, "JOB_TTL_S", 60)
    store = JobStore()
    running = store.create(["a"])
    running.status = "running"
    running.created_at = time.time() - 7200  # ancient but still running
    store._sweep()
    assert store.get(running.id) is running


def test_stored_job_cap(monkeypatch):
    monkeypatch.setattr(jobs_mod, "MAX_STORED_JOBS", 3)
    monkeypatch.setattr(jobs_mod, "MAX_ACTIVE_JOBS", 100)
    monkeypatch.setattr(jobs_mod, "JOB_TTL_S", 10**9)  # TTL never triggers
    store = JobStore()
    finished = []
    for i in range(5):
        j = store.create([f"r{i}"])
        j.status = "completed"
        j.finished_at = time.time() + i  # strictly increasing
        finished.append(j)
    store._sweep()
    assert len(store._jobs) == 3
    # Oldest finished jobs evicted first
    assert store.get(finished[0].id) is None
    assert store.get(finished[-1].id) is not None


def test_sync_endpoints_auto_queue_large_batches(monkeypatch):
    """/api/process must never run a large batch inside one HTTP request —
    beyond SYNC_MAX_REFS it returns 202 with a pollable job."""
    from fastapi.testclient import TestClient
    import api.app as app_mod

    monkeypatch.setattr(app_mod, "SYNC_MAX_REFS", 2)
    refs = "\n".join(
        f'[{i}] A. Author, "Paper number {i}," Some Journal, vol. {i}, 2020.'
        for i in range(1, 4)
    )

    with TestClient(app_mod.app) as client:
        r = client.post("/api/process", data={"references": refs})
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["queued"] is True and body["total"] == 3
        assert body["status_url"].endswith(body["job_id"])
        # Cancel immediately — the test must not wait on real processing.
        rc = client.delete(f"/api/jobs/{body['job_id']}")
        assert rc.status_code == 200

        # Small batches stay synchronous: an empty-input 400 proves the sync
        # path is still reachable without queueing.
        r2 = client.post("/api/process", data={"references": "   "})
        assert r2.status_code == 400


def test_cancel_marks_task():
    store = JobStore()
    job = store.create(["a"])
    job.status = "running"

    class FakeTask:
        cancelled = False
        def cancel(self):
            self.cancelled = True

    job.task = FakeTask()
    out = store.cancel(job.id)
    assert out is job and job.task.cancelled
    assert store.cancel("nope") is None
