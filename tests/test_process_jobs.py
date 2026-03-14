from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.services.process_jobs import (
    classify_process_job_health,
    parse_process_job_statuses,
    serialize_process_job,
)
from app.models.process_job import ProcessJobStatus
from app.utils.time import utcnow


def test_classify_process_job_health_warns_on_failures():
    assert classify_process_job_health(failed=1, stale=0) == "warning"


def test_parse_process_job_statuses_validates_input():
    statuses = parse_process_job_statuses(["failed", "succeeded"])

    assert statuses == [ProcessJobStatus.failed, ProcessJobStatus.succeeded]


def test_parse_process_job_statuses_rejects_unknown_value():
    with pytest.raises(ValueError):
        parse_process_job_statuses(["unknown"])


def test_serialize_process_job_marks_old_running_job_as_stale():
    now = utcnow()
    job = SimpleNamespace(
        id="job-1",
        job_type="picklist.complete",
        status=ProcessJobStatus.running,
        tenant_id="tenant-1",
        correlation_id="corr-1",
        request_meta={"pick_list_name": "PICK-0001", "create_delivery_note": True},
        result_meta={},
        error_message=None,
        created_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(minutes=10),
    )

    row = serialize_process_job(job, tenant_company_code="menor", now=now)

    assert row["pick_list_name"] == "PICK-0001"
    assert row["create_delivery_note"] is True
    assert row["is_stale"] is True
    assert row["status"] == "running"
