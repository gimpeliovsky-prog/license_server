from datetime import datetime

from pydantic import BaseModel, Field


class ProcessJobSummaryResponse(BaseModel):
    window_hours: int
    stale_after_minutes: int
    total: int
    pending: int
    running: int
    succeeded: int
    failed: int
    stale: int
    health_status: str
    has_alerts: bool


class ProcessJobListItemResponse(BaseModel):
    id: str
    job_type: str
    job_type_label: str
    status: str
    tenant_company_code: str
    correlation_id: str
    pick_list_name: str
    delivery_note_name: str
    create_delivery_note: bool
    error_message: str
    created_at: datetime
    updated_at: datetime
    age_minutes: int
    is_stale: bool


class ProcessJobListResponse(BaseModel):
    summary: ProcessJobSummaryResponse
    jobs: list[ProcessJobListItemResponse]
    window_hours: int
    status: str
    company_code: str | None = None
    q: str | None = None
    limit: int


class ProcessJobCleanupRequest(BaseModel):
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    statuses: list[str] | None = None
    limit: int | None = Field(default=None, ge=1, le=10000)
    dry_run: bool = False


class ProcessJobCleanupResponse(BaseModel):
    retention_days: int
    cutoff_time: datetime
    statuses: list[str] | None = None
    limit: int
    dry_run: bool
    matched_count: int
    deleted_count: int
