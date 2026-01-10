import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin
from app.models import Device, LicenseKey, LicenseKeyStatus, Tenant, TenantStatus
from app.schemas import (
    DeviceRevokeRequest,
    DeviceResponse,
    LicenseCreateRequest,
    LicenseResponse,
    LicenseStatusUpdateRequest,
    SubscriptionUpdateRequest,
    TenantCreateRequest,
    TenantResponse,
    TenantStatusUpdateRequest,
)
from app.services.license import fingerprint_license_key, hash_license_key
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


def get_tenant_or_404(db: Session, company_code: str) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.company_code == company_code).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


def serialize_tenant(tenant: Tenant) -> TenantResponse:
    return TenantResponse(
        id=tenant.id,
        company_code=tenant.company_code,
        erpnext_url=tenant.erpnext_url,
        status=tenant.status.value,
        subscription_expires_at=tenant.subscription_expires_at,
    )


@router.get("/tenants", response_model=list[TenantResponse])
def list_tenants(db: Session = Depends(get_db)) -> list[TenantResponse]:
    tenants = db.query(Tenant).order_by(Tenant.company_code.asc()).all()
    return [serialize_tenant(tenant) for tenant in tenants]


@router.get("/tenants/{company_code}", response_model=TenantResponse)
def show_tenant(company_code: str, db: Session = Depends(get_db)) -> TenantResponse:
    tenant = get_tenant_or_404(db, company_code)
    return serialize_tenant(tenant)


@router.post("/tenants", response_model=TenantResponse, status_code=201)
def create_tenant(payload: TenantCreateRequest, db: Session = Depends(get_db)) -> TenantResponse:
    existing = db.query(Tenant).filter(Tenant.company_code == payload.company_code).first()
    if existing:
        raise HTTPException(status_code=409, detail="Tenant already exists")

    tenant = Tenant(
        company_code=payload.company_code,
        erpnext_url=payload.erpnext_url.rstrip("/"),
        api_key=payload.api_key,
        api_secret=payload.api_secret,
        status=parse_tenant_status(payload.status),
        subscription_expires_at=payload.subscription_expires_at,
    )
    db.add(tenant)
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
        LicenseResponse(id=key.id, status=key.status.value, created_at=key.created_at, license_key=None)
        for key in licenses
    ]


@router.post("/licenses", response_model=LicenseResponse, status_code=201)
def create_license(payload: LicenseCreateRequest, db: Session = Depends(get_db)) -> LicenseResponse:
    tenant = get_tenant_or_404(db, payload.company_code)
    license_key = (payload.license_key or secrets.token_urlsafe(32)).strip()
    if not license_key:
        raise HTTPException(status_code=400, detail="License key invalid")
    fingerprint = fingerprint_license_key(license_key) or None

    license_entry = LicenseKey(
        tenant_id=tenant.id,
        hashed_key=hash_license_key(license_key),
        fingerprint=fingerprint,
        status=parse_license_status(payload.status),
    )
    db.add(license_entry)
    db.commit()
    db.refresh(license_entry)
    return LicenseResponse(
        id=license_entry.id,
        status=license_entry.status.value,
        created_at=license_entry.created_at,
        license_key=license_key,
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
