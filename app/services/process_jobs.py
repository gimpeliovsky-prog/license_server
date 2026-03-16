import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import ProcessJob, ProcessJobStatus, Tenant
from app.services.picklist_process import (
    PickListProcessError,
    create_delivery_note_from_pick_list,
    ensure_document_submitted,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)
settings = get_settings()

JOB_TYPE_PICKLIST_COMPLETE = "picklist.complete"
PROCESS_JOB_TYPE_LABELS = {
    JOB_TYPE_PICKLIST_COMPLETE: "Pick List completion",
}


def normalize_process_job_status(value: str | None) -> str:
    normalized = str(value or "all").strip().lower()
    return normalized if normalized in {"all", "pending", "running", "succeeded", "failed"} else "all"


def normalize_process_job_query(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip()


def get_stale_after_minutes() -> int:
    return max(1, settings.process_job_stale_after_minutes)


def is_process_job_stale(job: ProcessJob, *, now: datetime | None = None) -> bool:
    now = now or utcnow()
    stale_threshold = now - timedelta(minutes=get_stale_after_minutes())
    return job.status in {ProcessJobStatus.pending, ProcessJobStatus.running} and job.updated_at <= stale_threshold


def classify_process_job_health(*, failed: int, stale: int) -> str:
    if failed >= max(1, settings.process_job_failed_alert_threshold) or stale >= max(
        1, settings.process_job_stale_alert_threshold
    ):
        return "warning"
    return "ok"


def _base_process_job_query(
    db: Session,
    *,
    since_time: datetime | None = None,
    status: str = "all",
    company_code: str | None = None,
):
    query = db.query(ProcessJob).join(Tenant, ProcessJob.tenant_id == Tenant.id)
    if since_time is not None:
        query = query.filter(ProcessJob.created_at >= since_time)
    normalized_status = normalize_process_job_status(status)
    if normalized_status != "all":
        query = query.filter(ProcessJob.status == ProcessJobStatus(normalized_status))
    normalized_company = str(company_code or "").strip().lower()
    if normalized_company:
        query = query.filter(func.lower(Tenant.company_code) == normalized_company)
    return query


def build_process_job_summary(
    db: Session,
    *,
    window_hours: int,
    status: str = "all",
    company_code: str | None = None,
) -> dict[str, Any]:
    now = utcnow()
    since_time = now - timedelta(hours=max(1, window_hours))
    stale_after_minutes = get_stale_after_minutes()
    stale_threshold = now - timedelta(minutes=stale_after_minutes)
    base_query = _base_process_job_query(
        db,
        since_time=since_time,
        status=status,
        company_code=company_code,
    )

    total = base_query.count()
    counts = {
        "pending": base_query.filter(ProcessJob.status == ProcessJobStatus.pending).count(),
        "running": base_query.filter(ProcessJob.status == ProcessJobStatus.running).count(),
        "succeeded": base_query.filter(ProcessJob.status == ProcessJobStatus.succeeded).count(),
        "failed": base_query.filter(ProcessJob.status == ProcessJobStatus.failed).count(),
    }
    stale = (
        base_query.filter(ProcessJob.status.in_([ProcessJobStatus.pending, ProcessJobStatus.running]))
        .filter(ProcessJob.updated_at <= stale_threshold)
        .count()
    )
    health_status = classify_process_job_health(failed=counts["failed"], stale=stale)
    return {
        "window_hours": max(1, window_hours),
        "stale_after_minutes": stale_after_minutes,
        "total": total,
        "pending": counts["pending"],
        "running": counts["running"],
        "succeeded": counts["succeeded"],
        "failed": counts["failed"],
        "stale": stale,
        "health_status": health_status,
        "has_alerts": health_status != "ok",
    }


def serialize_process_job(job: ProcessJob, *, tenant_company_code: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    request_meta = job.request_meta if isinstance(job.request_meta, dict) else {}
    result_meta = job.result_meta if isinstance(job.result_meta, dict) else {}
    status_value = job.status.value
    age_minutes = max(0, int((now - job.updated_at).total_seconds() // 60))
    return {
        "id": str(job.id),
        "job_type": job.job_type,
        "job_type_label": PROCESS_JOB_TYPE_LABELS.get(job.job_type, job.job_type),
        "status": status_value,
        "tenant_company_code": tenant_company_code,
        "correlation_id": job.correlation_id or "-",
        "pick_list_name": str(request_meta.get("pick_list_name") or result_meta.get("pick_list_name") or "-"),
        "delivery_note_name": str(result_meta.get("delivery_note_name") or "-"),
        "create_delivery_note": bool(request_meta.get("create_delivery_note", False)),
        "error_message": (job.error_message or "").strip(),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "age_minutes": age_minutes,
        "is_stale": is_process_job_stale(job, now=now),
    }


def list_process_jobs(
    db: Session,
    *,
    window_hours: int,
    status: str = "all",
    company_code: str | None = None,
    search_query: str | None = None,
    limit: int = 200,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected_window_hours = max(1, window_hours)
    selected_limit = max(1, min(limit, 1000))
    jobs = (
        _base_process_job_query(
            db,
            since_time=utcnow() - timedelta(hours=selected_window_hours),
            status=status,
            company_code=company_code,
        )
        .order_by(ProcessJob.created_at.desc())
        .limit(selected_limit)
        .all()
    )
    tenant_ids = {job.tenant_id for job in jobs}
    tenant_code_map: dict[uuid.UUID, str] = {}
    if tenant_ids:
        tenants = db.query(Tenant.id, Tenant.company_code).filter(Tenant.id.in_(tenant_ids)).all()
        tenant_code_map = {tenant_id: code for tenant_id, code in tenants}

    now = utcnow()
    normalized_query = normalize_process_job_query(search_query).lower()
    rows: list[dict[str, Any]] = []
    for job in jobs:
        row = serialize_process_job(job, tenant_company_code=tenant_code_map.get(job.tenant_id, "-"), now=now)
        if normalized_query:
            haystack = " ".join(
                [
                    row["id"],
                    row["job_type"],
                    row["tenant_company_code"],
                    row["correlation_id"],
                    row["pick_list_name"],
                    row["delivery_note_name"],
                    row["error_message"],
                ]
            ).lower()
            if normalized_query not in haystack:
                continue
        rows.append(row)

    summary = build_process_job_summary(
        db,
        window_hours=selected_window_hours,
        status=status,
        company_code=company_code,
    )
    return rows, summary


def parse_process_job_statuses(values: list[str] | None) -> list[ProcessJobStatus] | None:
    if not values:
        return None
    parsed: list[ProcessJobStatus] = []
    for value in values:
        normalized = str(value or "").strip().lower()
        if not normalized:
            continue
        try:
            parsed.append(ProcessJobStatus(normalized))
        except ValueError as exc:
            raise ValueError(f"Invalid process job status: {value}") from exc
    return parsed or None


def cleanup_process_jobs(
    db: Session,
    *,
    retention_days: int | None = None,
    statuses: list[ProcessJobStatus] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if statuses and any(status in {ProcessJobStatus.pending, ProcessJobStatus.running} for status in statuses):
        raise ValueError("Cleanup does not allow pending or running process jobs")
    selected_retention_days = retention_days or settings.process_job_retention_days
    selected_limit = limit or settings.process_job_cleanup_delete_limit
    cutoff_time = utcnow() - timedelta(days=max(1, selected_retention_days))
    query = db.query(ProcessJob).filter(ProcessJob.created_at < cutoff_time)
    if statuses:
        query = query.filter(ProcessJob.status.in_(statuses))
    jobs = query.order_by(ProcessJob.created_at.asc()).limit(max(1, min(selected_limit, 10000))).all()
    matched_count = len(jobs)
    logger.info(
        "Process job cleanup requested: retention_days=%s statuses=%s limit=%s dry_run=%s matched=%s",
        selected_retention_days,
        [status.value for status in statuses] if statuses else None,
        max(1, min(selected_limit, 10000)),
        dry_run,
        matched_count,
    )
    if not dry_run and jobs:
        for job in jobs:
            db.delete(job)
        db.commit()
    return {
        "retention_days": selected_retention_days,
        "cutoff_time": cutoff_time,
        "statuses": [status.value for status in statuses] if statuses else None,
        "limit": max(1, min(selected_limit, 10000)),
        "dry_run": dry_run,
        "matched_count": matched_count,
        "deleted_count": 0 if dry_run else matched_count,
    }


def create_process_job(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    device_id: uuid.UUID | None,
    job_type: str,
    correlation_id: str | None,
    request_key: str | None = None,
    request_meta: dict[str, Any] | None = None,
) -> ProcessJob:
    job = ProcessJob(
        tenant_id=tenant_id,
        device_id=device_id,
        job_type=job_type,
        status=ProcessJobStatus.pending,
        correlation_id=correlation_id,
        request_key=request_key,
        request_meta=request_meta,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if request_key:
            existing = db.query(ProcessJob).filter(ProcessJob.request_key == request_key).first()
            if existing:
                return existing
        raise
    db.refresh(job)
    return job


def requeue_process_job(db: Session, *, job_id: uuid.UUID) -> ProcessJob:
    job = db.query(ProcessJob).filter(ProcessJob.id == job_id).first()
    if not job:
        raise LookupError("Process job not found")
    if job.status != ProcessJobStatus.running:
        raise ValueError("Only running process jobs can be requeued")
    if not is_process_job_stale(job):
        raise ValueError("Only stale running process jobs can be requeued")

    job.status = ProcessJobStatus.pending
    job.error_message = None
    db.commit()
    db.refresh(job)
    return job


def run_picklist_complete_job(job_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        job = db.query(ProcessJob).filter(ProcessJob.id == job_id).first()
        if not job:
            return
        if job.status != ProcessJobStatus.pending:
            return

        job.status = ProcessJobStatus.running
        job.error_message = None
        db.commit()

        tenant = db.query(Tenant).filter(Tenant.id == job.tenant_id).first()
        if not tenant:
            raise PickListProcessError("Tenant not found", status_code=404)

        request_meta = job.request_meta or {}
        pick_list_name = str(request_meta.get("pick_list_name") or "").strip()
        if not pick_list_name:
            raise PickListProcessError("Pick List is required", status_code=400)

        create_delivery_note = bool(request_meta.get("create_delivery_note", True))
        ensure_document_submitted(tenant, "Pick List", pick_list_name)
        delivery_note_name = None
        if create_delivery_note:
            delivery_note_name = create_delivery_note_from_pick_list(tenant, pick_list_name)

        job.status = ProcessJobStatus.succeeded
        job.result_meta = {
            "pick_list_name": pick_list_name,
            "delivery_note_created": delivery_note_name is not None,
            "delivery_note_name": delivery_note_name,
        }
        job.error_message = None
        db.commit()
    except Exception as exc:
        logger.exception("Process job failed: %s", job_id)
        job = db.query(ProcessJob).filter(ProcessJob.id == job_id).first()
        if job:
            job.status = ProcessJobStatus.failed
            job.error_message = str(exc)
            existing_meta = job.result_meta if isinstance(job.result_meta, dict) else {}
            if isinstance(exc, PickListProcessError):
                job.result_meta = {
                    **existing_meta,
                    "error_code": exc.reason_code,
                    "retryable": exc.retryable,
                    "erp_refused": exc.erp_refused,
                }
            else:
                job.result_meta = {
                    **existing_meta,
                    "error_code": "process_job_failed",
                    "retryable": False,
                    "erp_refused": False,
                }
            db.commit()
    finally:
        db.close()
