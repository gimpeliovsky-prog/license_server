from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import SessionLocal
from app.services.crm_identity_sync import sync_all_active_tenants

logger = logging.getLogger(__name__)

_runner_task: asyncio.Task | None = None
_runner_stop_event: asyncio.Event | None = None


def _next_run_delay_seconds() -> float:
    settings = get_settings()
    tz = ZoneInfo(settings.crm_identity_sync_timezone)
    now = datetime.now(tz)
    next_run = now.replace(
        hour=settings.crm_identity_sync_hour,
        minute=settings.crm_identity_sync_minute,
        second=0,
        microsecond=0,
    )
    if next_run <= now:
        next_run = next_run + timedelta(days=1)
    return max((next_run - now).total_seconds(), 1.0)


def _run_sync_once() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        results = sync_all_active_tenants(db, page_size=settings.crm_identity_sync_page_size)
        logger.info("CRM identity sync completed: %s", results)
    finally:
        db.close()


async def _runner_loop() -> None:
    assert _runner_stop_event is not None
    logger.info(
        "CRM identity sync runner started; next run daily at %02d:%02d %s",
        get_settings().crm_identity_sync_hour,
        get_settings().crm_identity_sync_minute,
        get_settings().crm_identity_sync_timezone,
    )
    while not _runner_stop_event.is_set():
        delay = _next_run_delay_seconds()
        try:
            await asyncio.wait_for(_runner_stop_event.wait(), timeout=delay)
            break
        except asyncio.TimeoutError:
            pass
        try:
            await asyncio.to_thread(_run_sync_once)
        except Exception:
            logger.exception("CRM identity sync runner iteration failed")
    logger.info("CRM identity sync runner stopped")


async def start_crm_identity_sync_runner() -> None:
    global _runner_task, _runner_stop_event
    settings = get_settings()
    if not settings.crm_identity_sync_enabled:
        logger.info("CRM identity sync runner disabled by config")
        return
    if _runner_task and not _runner_task.done():
        return
    _runner_stop_event = asyncio.Event()
    _runner_task = asyncio.create_task(_runner_loop())


async def stop_crm_identity_sync_runner() -> None:
    global _runner_task, _runner_stop_event
    if _runner_stop_event is not None:
        _runner_stop_event.set()
    if _runner_task is not None:
        try:
            await _runner_task
        finally:
            _runner_task = None
            _runner_stop_event = None
