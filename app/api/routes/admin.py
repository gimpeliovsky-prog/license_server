import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, get_db, require_admin
from app.models import Device, LicenseKey, LicenseKeyStatus, Tenant, TenantStatus
from app.schemas import (
    DeviceRevokeRequest,
    DeviceResponse,
    LicenseCreateRequest,
    LicenseDiagnosticsLicenseInfo,
    LicenseDiagnosticsRequest,
    LicenseDiagnosticsResponse,
    LicenseDiagnosticsTenantInfo,
    LicenseResponse,
    LicenseStatusUpdateRequest,
    ProcessJobCleanupRequest,
    ProcessJobCleanupResponse,
    ProcessJobListItemResponse,
    ProcessJobListResponse,
    ProcessJobSummaryResponse,
    SubscriptionUpdateRequest,
    TenantCreateRequest,
    TenantCredentialsUpdateRequest,
    TenantResponse,
    TenantSystemUpdateRequest,
    TenantStatusUpdateRequest,
)
from app.services.erpnext import (
    ERPNextValidationError,
    normalize_erpnext_url,
    validate_erpnext_credentials,
)
from app.services.license import (
    fingerprint_license_key,
    hash_license_key,
    verify_license_key_flexible,
)
from app.services.process_jobs import (
    build_process_job_summary,
    cleanup_process_jobs,
    list_process_jobs,
    normalize_process_job_status,
    parse_process_job_statuses,
    requeue_process_job,
    serialize_process_job,
)
from app.services.process_job_runner import notify_process_job_runner
from app.services.tenant_cleanup import delete_tenant_with_dependencies
from app.utils.time import utcnow

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def parse_tenant_status(value: str) -> TenantStatus:
    try:
        return TenantStatus(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid tenant status") from exc


def parse_license_status(value: str) -> LicenseKeyStatus:
    try:
        return LicenseKeyStatus(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid license status") from exc


def normalize_license_description(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def get_tenant_or_404(db: Session, company_code: str) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.company_code == company_code).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


def serialize_tenant(tenant: Tenant) -> TenantResponse:
    return TenantResponse(
        id=tenant.id,
        company_code=tenant.company_code,
        company_name=tenant.company_name,
        erpnext_url=tenant.erpnext_url,
        status=tenant.status.value,
        is_system=tenant.is_system,
        subscription_expires_at=tenant.subscription_expires_at,
    )


def _license_candidates(
    db: Session,
    key_fingerprint: str | None,
    *,
    tenant_id: uuid.UUID | None,
    only_active: bool,
) -> list[LicenseKey]:
    query = db.query(LicenseKey)
    if tenant_id is not None:
        query = query.filter(LicenseKey.tenant_id == tenant_id)
    if only_active:
        query = query.filter(LicenseKey.status == LicenseKeyStatus.active)

    if key_fingerprint:
        by_fingerprint = query.filter(LicenseKey.fingerprint == key_fingerprint).all()
        if by_fingerprint:
            return by_fingerprint

    return query.filter(LicenseKey.fingerprint.is_(None)).all()


def _find_matching_license(
    db: Session,
    raw_key: str,
    key_fingerprint: str | None,
    *,
    tenant_id: uuid.UUID | None = None,
    only_active: bool = False,
) -> LicenseKey | None:
    candidates = _license_candidates(
        db=db,
        key_fingerprint=key_fingerprint,
        tenant_id=tenant_id,
        only_active=only_active,
    )
    return next(
        (entry for entry in candidates if verify_license_key_flexible(raw_key, entry.hashed_key)),
        None,
    )


def _serialize_diagnostic_tenant(tenant: Tenant | None, now) -> LicenseDiagnosticsTenantInfo:
    if not tenant:
        return LicenseDiagnosticsTenantInfo()
    return LicenseDiagnosticsTenantInfo(
        id=tenant.id,
        company_code=tenant.company_code,
        status=tenant.status.value,
        subscription_expires_at=tenant.subscription_expires_at,
        subscription_active=tenant.is_system or tenant.subscription_expires_at >= now,
    )


def _serialize_diagnostic_license(entry: LicenseKey | None) -> LicenseDiagnosticsLicenseInfo | None:
    if not entry:
        return None
    tenant = entry.tenant
    return LicenseDiagnosticsLicenseInfo(
        id=entry.id,
        status=entry.status.value,
        tenant_id=entry.tenant_id,
        tenant_company_code=tenant.company_code if tenant else None,
        created_at=entry.created_at,
        fingerprint=entry.fingerprint,
    )


@router.get("/tenants", response_model=list[TenantResponse])
def list_tenants(db: Session = Depends(get_db)) -> list[TenantResponse]:
    tenants = db.query(Tenant).order_by(Tenant.company_code.asc()).all()
    return [serialize_tenant(tenant) for tenant in tenants]


@router.get("/process-jobs", response_model=ProcessJobListResponse)
def list_process_jobs_admin(
    window_hours: int = 24,
    status: str = "all",
    company_code: str | None = None,
    q: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
) -> ProcessJobListResponse:
    normalized_status = normalize_process_job_status(status)
    jobs, summary = list_process_jobs(
        db,
        window_hours=window_hours,
        status=normalized_status,
        company_code=company_code,
        search_query=q,
        limit=limit,
    )
    return ProcessJobListResponse(
        summary=ProcessJobSummaryResponse(**summary),
        jobs=[ProcessJobListItemResponse(**row) for row in jobs],
        window_hours=max(1, window_hours),
        status=normalized_status,
        company_code=(company_code or "").strip() or None,
        q=(q or "").strip() or None,
        limit=max(1, min(limit, 1000)),
    )


@router.get("/process-jobs/summary", response_model=ProcessJobSummaryResponse)
def get_process_jobs_summary_admin(
    window_hours: int = 24,
    status: str = "all",
    company_code: str | None = None,
    db: Session = Depends(get_db),
) -> ProcessJobSummaryResponse:
    normalized_status = normalize_process_job_status(status)
    summary = build_process_job_summary(
        db,
        window_hours=window_hours,
        status=normalized_status,
        company_code=company_code,
    )
    return ProcessJobSummaryResponse(**summary)


@router.post("/process-jobs/cleanup", response_model=ProcessJobCleanupResponse)
def cleanup_process_jobs_admin(
    payload: ProcessJobCleanupRequest,
    db: Session = Depends(get_db),
) -> ProcessJobCleanupResponse:
    try:
        statuses = parse_process_job_statuses(payload.statuses)
        result = cleanup_process_jobs(
            db,
            retention_days=payload.retention_days,
            statuses=statuses,
            limit=payload.limit,
            dry_run=payload.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProcessJobCleanupResponse(**result)


@router.post("/process-jobs/{job_id}/requeue", response_model=ProcessJobListItemResponse)
def requeue_process_job_admin(job_id: str, db: Session = Depends(get_db)) -> ProcessJobListItemResponse:
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid process job id") from exc

    try:
        job = requeue_process_job(db, job_id=job_uuid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    notify_process_job_runner()
    tenant = db.query(Tenant.company_code).filter(Tenant.id == job.tenant_id).first()
    tenant_company_code = tenant[0] if tenant else "-"
    return ProcessJobListItemResponse(**serialize_process_job(job, tenant_company_code=tenant_company_code))


@router.get("/tenants/{company_code}", response_model=TenantResponse)
def show_tenant(company_code: str, db: Session = Depends(get_db)) -> TenantResponse:
    tenant = get_tenant_or_404(db, company_code)
    return serialize_tenant(tenant)


@router.post("/tenants", response_model=TenantResponse, status_code=201)
def create_tenant(payload: TenantCreateRequest, db: Session = Depends(get_db)) -> TenantResponse:
    existing = db.query(Tenant).filter(Tenant.company_code == payload.company_code).first()
    if existing:
        raise HTTPException(status_code=409, detail="Tenant already exists")

    normalized_url = normalize_erpnext_url(payload.erpnext_url)
    if not normalized_url:
        raise HTTPException(status_code=400, detail="ERPNext URL is required")

    try:
        validate_erpnext_credentials(normalized_url, payload.api_key, payload.api_secret)
    except ERPNextValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    tenant = Tenant(
        company_code=payload.company_code,
        company_name=(payload.company_name or "").strip() or None,
        erpnext_url=normalized_url,
        api_key=payload.api_key,
        api_secret=payload.api_secret,
        status=parse_tenant_status(payload.status),
        is_system=payload.is_system,
        subscription_expires_at=payload.subscription_expires_at,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return serialize_tenant(tenant)


@router.patch("/tenants/{company_code}/credentials", response_model=TenantResponse)
def update_credentials(
    company_code: str, payload: TenantCredentialsUpdateRequest, db: Session = Depends(get_db)
) -> TenantResponse:
    if not payload.erpnext_url and not payload.api_key and not payload.api_secret:
        raise HTTPException(status_code=400, detail="Provide at least one field to update")

    tenant = get_tenant_or_404(db, company_code)

    new_url = normalize_erpnext_url(payload.erpnext_url) if payload.erpnext_url else tenant.erpnext_url
    if payload.erpnext_url and not new_url:
        raise HTTPException(status_code=400, detail="ERPNext URL is required")

    new_api_key = payload.api_key if payload.api_key is not None else tenant.api_key
    new_api_secret = payload.api_secret if payload.api_secret is not None else tenant.api_secret

    try:
        validate_erpnext_credentials(new_url, new_api_key, new_api_secret)
    except ERPNextValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    tenant.erpnext_url = new_url
    tenant.api_key = new_api_key
    tenant.api_secret = new_api_secret
    db.commit()
    db.refresh(tenant)
    return serialize_tenant(tenant)


@router.patch("/tenants/{company_code}/status", response_model=TenantResponse)
def update_status(
    company_code: str, payload: TenantStatusUpdateRequest, db: Session = Depends(get_db)
) -> TenantResponse:
    tenant = get_tenant_or_404(db, company_code)
    tenant.status = parse_tenant_status(payload.status)
    db.commit()
    db.refresh(tenant)
    return serialize_tenant(tenant)


@router.patch("/tenants/{company_code}/system", response_model=TenantResponse)
def update_system_flag(
    company_code: str, payload: TenantSystemUpdateRequest, db: Session = Depends(get_db)
) -> TenantResponse:
    tenant = get_tenant_or_404(db, company_code)
    tenant.is_system = bool(payload.is_system)
    db.commit()
    db.refresh(tenant)
    return serialize_tenant(tenant)


@router.patch("/tenants/{company_code}/subscription", response_model=TenantResponse)
def update_subscription(
    company_code: str, payload: SubscriptionUpdateRequest, db: Session = Depends(get_db)
) -> TenantResponse:
    tenant = get_tenant_or_404(db, company_code)

    if payload.expires_at and payload.add_days:
        raise HTTPException(status_code=400, detail="Use expires_at or add_days, not both")
    if not payload.expires_at and payload.add_days is None:
        raise HTTPException(status_code=400, detail="Provide expires_at or add_days")

    if payload.expires_at:
        tenant.subscription_expires_at = payload.expires_at
    else:
        now = utcnow()
        base = tenant.subscription_expires_at
        if base < now:
            base = now
        tenant.subscription_expires_at = base + timedelta(days=payload.add_days)

    db.commit()
    db.refresh(tenant)
    return serialize_tenant(tenant)


@router.delete("/tenants/{company_code}", status_code=204)
def delete_tenant(company_code: str, db: Session = Depends(get_db)) -> None:
    tenant = get_tenant_or_404(db, company_code)
    delete_tenant_with_dependencies(db, tenant)
    db.commit()
    return None


@router.get("/tenants/{company_code}/licenses", response_model=list[LicenseResponse])
def list_licenses(company_code: str, db: Session = Depends(get_db)) -> list[LicenseResponse]:
    tenant = get_tenant_or_404(db, company_code)
    licenses = (
        db.query(LicenseKey)
        .filter(LicenseKey.tenant_id == tenant.id)
        .order_by(LicenseKey.created_at.desc())
        .all()
    )
    return [
        LicenseResponse(
            id=key.id,
            status=key.status.value,
            created_at=key.created_at,
            license_key=None,
            description=key.description,
        )
        for key in licenses
    ]


@router.post("/licenses", response_model=LicenseResponse, status_code=201)
def create_license(payload: LicenseCreateRequest, db: Session = Depends(get_db)) -> LicenseResponse:
    tenant = get_tenant_or_404(db, payload.company_code)
    license_key = (payload.license_key or secrets.token_urlsafe(32)).strip()
    if not license_key:
        raise HTTPException(status_code=400, detail="License key invalid")
    fingerprint = fingerprint_license_key(license_key) or None
    if fingerprint:
        existing = db.query(LicenseKey).filter(LicenseKey.fingerprint == fingerprint).first()
        if existing:
            raise HTTPException(status_code=400, detail="License key already exists")

    license_entry = LicenseKey(
        tenant_id=tenant.id,
        hashed_key=hash_license_key(license_key),
        fingerprint=fingerprint,
        status=parse_license_status(payload.status),
        description=normalize_license_description(payload.description),
    )
    db.add(license_entry)
    db.commit()
    db.refresh(license_entry)
    return LicenseResponse(
        id=license_entry.id,
        status=license_entry.status.value,
        created_at=license_entry.created_at,
        license_key=license_key,
        description=license_entry.description,
    )


@router.patch("/licenses/{license_id}/status", response_model=LicenseResponse)
def update_license_status(
    license_id: str, payload: LicenseStatusUpdateRequest, db: Session = Depends(get_db)
) -> LicenseResponse:
    try:
        license_uuid = uuid.UUID(license_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid license id")

    license_entry = db.query(LicenseKey).filter(LicenseKey.id == license_uuid).first()
    if not license_entry:
        raise HTTPException(status_code=404, detail="License key not found")

    license_entry.status = parse_license_status(payload.status)
    db.commit()
    db.refresh(license_entry)

    return LicenseResponse(
        id=license_entry.id,
        status=license_entry.status.value,
        created_at=license_entry.created_at,
        license_key=None,
        description=license_entry.description,
    )


@router.delete("/licenses/{license_id}", status_code=204)
def delete_license(license_id: str, db: Session = Depends(get_db)) -> None:
    try:
        license_uuid = uuid.UUID(license_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid license id")

    license_entry = db.query(LicenseKey).filter(LicenseKey.id == license_uuid).first()
    if not license_entry:
        raise HTTPException(status_code=404, detail="License key not found")

    db.delete(license_entry)
    db.commit()
    return None


@router.post("/licenses/diagnostics", response_model=LicenseDiagnosticsResponse)
def diagnose_license(
    payload: LicenseDiagnosticsRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> LicenseDiagnosticsResponse:
    raw_key = payload.license_key.strip()
    if not raw_key:
        raise HTTPException(status_code=400, detail="License key is required")

    now = utcnow()
    input_company_code = payload.company_code.strip() if payload.company_code else None
    normalized_company_code = input_company_code.lower() if input_company_code else None
    key_fingerprint = fingerprint_license_key(raw_key) or None

    matched_active_global = _find_matching_license(
        db=db,
        raw_key=raw_key,
        key_fingerprint=key_fingerprint,
        only_active=True,
    )
    matched_any_global = _find_matching_license(
        db=db,
        raw_key=raw_key,
        key_fingerprint=key_fingerprint,
        only_active=False,
    )

    resolved_tenant: Tenant | None = None
    matched_license: LicenseKey | None = None
    valid = False
    reason_code = ""
    reason_detail = ""

    if normalized_company_code:
        tenant = (
            db.query(Tenant)
            .filter(func.lower(Tenant.company_code) == normalized_company_code)
            .first()
        )
        resolved_tenant = tenant

        if not tenant:
            reason_code = "tenant_not_found"
            reason_detail = "Tenant with provided company code does not exist."
        elif tenant.status != TenantStatus.active:
            reason_code = "tenant_not_active"
            reason_detail = f"Tenant status is '{tenant.status.value}'."
        else:
            matched_active_in_company = _find_matching_license(
                db=db,
                raw_key=raw_key,
                key_fingerprint=key_fingerprint,
                tenant_id=tenant.id,
                only_active=True,
            )
            matched_any_in_company = _find_matching_license(
                db=db,
                raw_key=raw_key,
                key_fingerprint=key_fingerprint,
                tenant_id=tenant.id,
                only_active=False,
            )

            if matched_active_in_company:
                matched_license = matched_active_in_company
                if tenant.subscription_expires_at < now and not tenant.is_system:
                    reason_code = "subscription_expired"
                    reason_detail = "Tenant subscription is expired."
                else:
                    valid = True
                    reason_code = "ok"
                    reason_detail = "License is valid for activation."
            elif matched_any_in_company:
                matched_license = matched_any_in_company
                reason_code = "license_revoked"
                reason_detail = "License key exists for tenant, but status is not active."
            elif matched_any_global:
                matched_license = matched_any_global
                other_company = (
                    matched_any_global.tenant.company_code
                    if matched_any_global.tenant
                    else None
                )
                reason_code = "license_belongs_other_tenant"
                reason_detail = (
                    f"License key belongs to another tenant ({other_company})."
                    if other_company
                    else "License key belongs to another tenant."
                )
            else:
                reason_code = "license_not_found_for_company"
                reason_detail = "License key does not match any license in provided company code."
    else:
        if matched_active_global:
            matched_license = matched_active_global
            resolved_tenant = matched_active_global.tenant
            if not resolved_tenant or resolved_tenant.status != TenantStatus.active:
                reason_code = "tenant_not_active"
                reason_detail = (
                    "Tenant for this license is missing."
                    if not resolved_tenant
                    else f"Tenant status is '{resolved_tenant.status.value}'."
                )
            elif resolved_tenant.subscription_expires_at < now and not resolved_tenant.is_system:
                reason_code = "subscription_expired"
                reason_detail = "Tenant subscription is expired."
            else:
                valid = True
                reason_code = "ok"
                reason_detail = "License is valid for activation."
        elif matched_any_global:
            matched_license = matched_any_global
            resolved_tenant = matched_any_global.tenant
            reason_code = "license_revoked"
            reason_detail = "License key exists, but status is not active."
        else:
            reason_code = "license_not_found"
            reason_detail = "License key is not found."

    audit_tenant_id = (
        resolved_tenant.id
        if resolved_tenant
        else (matched_license.tenant_id if matched_license else None)
    )
    db.add(
        AuditLog(
            tenant_id=audit_tenant_id,
            device_id=None,
            action="license_diagnostics",
            meta={
                "ip": get_client_ip(request),
                "valid": valid,
                "reason_code": reason_code,
                "reason_detail": reason_detail,
                "input_company_code": input_company_code,
                "normalized_company_code": normalized_company_code,
                "key_fingerprint": key_fingerprint,
                "matched_license_id": str(matched_license.id) if matched_license else None,
                "matched_license_status": matched_license.status.value if matched_license else None,
                "matched_license_tenant_id": (
                    str(matched_license.tenant_id) if matched_license else None
                ),
            },
        )
    )
    db.commit()

    return LicenseDiagnosticsResponse(
        valid=valid,
        reason_code=reason_code,
        reason_detail=reason_detail,
        server_time=now,
        input_company_code=input_company_code,
        normalized_company_code=normalized_company_code,
        key_fingerprint=key_fingerprint,
        tenant=_serialize_diagnostic_tenant(resolved_tenant, now),
        matched_license=_serialize_diagnostic_license(matched_license),
    )


@router.get("/tenants/{company_code}/devices", response_model=list[DeviceResponse])
def list_devices(company_code: str, db: Session = Depends(get_db)) -> list[DeviceResponse]:
    tenant = get_tenant_or_404(db, company_code)
    devices = (
        db.query(Device)
        .filter(Device.tenant_id == tenant.id)
        .order_by(Device.created_at.desc())
        .all()
    )
    return [
        DeviceResponse(device_id=device.device_id, revoked=device.revoked, last_seen=device.last_seen)
        for device in devices
    ]


@router.patch("/tenants/{company_code}/devices/{device_id}", response_model=DeviceResponse)
def update_device(
    company_code: str, device_id: str, payload: DeviceRevokeRequest, db: Session = Depends(get_db)
) -> DeviceResponse:
    tenant = get_tenant_or_404(db, company_code)
    device = (
        db.query(Device)
        .filter(Device.tenant_id == tenant.id, Device.device_id == device_id)
        .first()
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    device.revoked = payload.revoked
    db.commit()
    db.refresh(device)
    return DeviceResponse(device_id=device.device_id, revoked=device.revoked, last_seen=device.last_seen)
