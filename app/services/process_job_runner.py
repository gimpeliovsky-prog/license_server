import asyncio
import logging
import uuid

from app.config import get_settings
from app.db import SessionLocal
from app.models import ProcessJob, ProcessJobStatus
from app.services.process_jobs import run_picklist_complete_job

logger = logging.getLogger(__name__)
settings = get_settings()

_runner_task: asyncio.Task | None = None
_runner_stop_event: asyncio.Event | None = None
_runner_wakeup_event: asyncio.Event | None = None


def claim_next_process_job() -> uuid.UUID | None:
    db = SessionLocal()
    try:
        job = (
            db.query(ProcessJob)
            .filter(ProcessJob.status == ProcessJobStatus.pending)
            .order_by(ProcessJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if not job:
            db.rollback()
            return None

        job.status = ProcessJobStatus.running
        job.error_message = None
        db.commit()
        return job.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_next_process_job() -> bool:
    job_id = claim_next_process_job()
    if not job_id:
        return False
    run_picklist_complete_job(job_id)
    return True


async def _runner_loop() -> None:
    assert _runner_stop_event is not None
    assert _runner_wakeup_event is not None
    poll_seconds = max(1, settings.process_job_runner_poll_seconds)
    logger.info("Process job runner started with poll interval %ss", poll_seconds)
    while not _runner_stop_event.is_set():
        processed = await asyncio.to_thread(run_next_process_job)
        if processed:
            continue
        try:
            await asyncio.wait_for(_runner_wakeup_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            _runner_wakeup_event.clear()
    logger.info("Process job runner stopped")


def notify_process_job_runner() -> None:
    if _runner_wakeup_event is not None:
        _runner_wakeup_event.set()


async def start_process_job_runner() -> None:
    global _runner_task, _runner_stop_event, _runner_wakeup_event
    if not settings.process_job_runner_enabled:
        logger.info("Process job runner disabled by config")
        return
    if _runner_task is not None and not _runner_task.done():
        return
    _runner_stop_event = asyncio.Event()
    _runner_wakeup_event = asyncio.Event()
    _runner_wakeup_event.set()
    _runner_task = asyncio.create_task(_runner_loop())


async def stop_process_job_runner() -> None:
    global _runner_task, _runner_stop_event, _runner_wakeup_event
    if _runner_task is None:
        return
    if _runner_stop_event is not None:
        _runner_stop_event.set()
    if _runner_wakeup_event is not None:
        _runner_wakeup_event.set()
    try:
        await _runner_task
    finally:
        _runner_task = None
        _runner_stop_event = None
        _runner_wakeup_event = None
