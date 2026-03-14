from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.process_job import ProcessJobStatus
from app.services.process_jobs import cleanup_process_jobs, create_process_job, requeue_process_job


def test_cleanup_rejects_pending_and_running_statuses():
    with pytest.raises(ValueError):
        cleanup_process_jobs(
            db=SimpleNamespace(),
            statuses=[ProcessJobStatus.pending],
            dry_run=True,
        )


class _FakeQuery:
    def __init__(self, existing):
        self._existing = existing

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._existing


class _FakeDb:
    def __init__(self, existing_job):
        self.existing_job = existing_job
        self.added = None
        self.commits = 0

    def add(self, value):
        self.added = value

    def commit(self):
        self.commits += 1
        if self.commits == 1:
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("duplicate", params=None, orig=None)

    def rollback(self):
        return None

    def query(self, model):
        return _FakeQuery(self.existing_job)

    def refresh(self, value):
        return None


class _FakeJobQuery:
    def __init__(self, job):
        self._job = job

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._job


class _FakeRequeueDb:
    def __init__(self, job):
        self.job = job
        self.committed = False
        self.refreshed = False

    def query(self, model):
        return _FakeJobQuery(self.job)

    def commit(self):
        self.committed = True

    def refresh(self, value):
        self.refreshed = True


def test_create_process_job_returns_existing_job_on_request_key_conflict():
    existing_job = SimpleNamespace(id=uuid4(), request_key="same-key")
    db = _FakeDb(existing_job)

    job = create_process_job(
        db,
        tenant_id=uuid4(),
        device_id=None,
        job_type="picklist.complete",
        correlation_id="corr",
        request_key="same-key",
        request_meta={"pick_list_name": "PICK-1"},
    )

    assert job is existing_job


def test_requeue_process_job_rejects_non_stale_running_job():
    job = SimpleNamespace(
        id=uuid4(),
        status=ProcessJobStatus.running,
        updated_at=datetime.now(timezone.utc),
        error_message="timeout",
    )
    db = _FakeRequeueDb(job)

    with pytest.raises(ValueError):
        requeue_process_job(db, job_id=job.id)


def test_requeue_process_job_moves_stale_running_job_to_pending():
    job = SimpleNamespace(
        id=uuid4(),
        status=ProcessJobStatus.running,
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        error_message="timeout",
    )
    db = _FakeRequeueDb(job)

    result = requeue_process_job(db, job_id=job.id)

    assert result is job
    assert job.status == ProcessJobStatus.pending
    assert job.error_message is None
    assert db.committed is True
    assert db.refreshed is True
