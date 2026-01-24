from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, get_db, get_request_context, rate_limit_activate, rate_limit_refresh
from app.models import AuditLog, Device, LicenseKey, LicenseKeyStatus, Tenant, TenantStatus
from app.schemas import ActivateRequest, TokenResponse
from app.services.auth import create_access_token
from app.services.license import fingerprint_license_key, verify_license_key_flexible
from app.utils.time import utcnow

router = APIRouter(tags=["auth"])


@router.post("/activate", response_model=TokenResponse)
def activate(payload: ActivateRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    raw_key = payload.license_key.strip()
    if not raw_key:
        raise HTTPException(status_code=401, detail="License key invalid")

    company_code = payload.company_code.strip() if payload.company_code else None
    company_code_norm = company_code.lower() if company_code else None
    rate_limit_key = company_code_norm or f"license:{raw_key}"
    rate_limit_activate(request, rate_limit_key)

    now = utcnow()
    tenant: Tenant | None = None
    fingerprint = fingerprint_license_key(raw_key)

    if company_code_norm:
        tenant = (
            db.query(Tenant)
            .filter(func.lower(Tenant.company_code) == company_code_norm)
            .first()
        )
        if not tenant or tenant.status != TenantStatus.active:
            raise HTTPException(status_code=404, detail="Tenant not found")

        if tenant.subscription_expires_at < now:
            raise HTTPException(status_code=403, detail="Subscription expired")

        active_keys: list[LicenseKey] = []
        if fingerprint:
            active_keys = (
                db.query(LicenseKey)
                .filter(
                    LicenseKey.tenant_id == tenant.id,
                    LicenseKey.status == LicenseKeyStatus.active,
                    LicenseKey.fingerprint == fingerprint,
                )
                .all()
            )
        if not active_keys:
            active_keys = (
                db.query(LicenseKey)
                .filter(
                    LicenseKey.tenant_id == tenant.id,
                    LicenseKey.status == LicenseKeyStatus.active,
                    LicenseKey.fingerprint.is_(None),
                )
                .all()
            )
        if not active_keys:
            raise HTTPException(status_code=401, detail="License key invalid")

        matched_key = next(
            (key for key in active_keys if verify_license_key_flexible(raw_key, key.hashed_key)),
            None,
        )
        if not matched_key:
            raise HTTPException(status_code=401, detail="License key invalid")
        if fingerprint and not matched_key.fingerprint:
            matched_key.fingerprint = fingerprint
    else:
        active_keys: list[LicenseKey] = []
        if fingerprint:
            active_keys = (
                db.query(LicenseKey)
                .filter(
                    LicenseKey.status == LicenseKeyStatus.active,
                    LicenseKey.fingerprint == fingerprint,
                )
                .all()
            )
        if not active_keys:
            active_keys = (
                db.query(LicenseKey)
                .filter(
                    LicenseKey.status == LicenseKeyStatus.active,
                    LicenseKey.fingerprint.is_(None),
                )
                .all()
            )
        matched_key = next(
            (key for key in active_keys if verify_license_key_flexible(raw_key, key.hashed_key)),
            None,
        )
        if not matched_key:
            raise HTTPException(status_code=401, detail="License key invalid")

        tenant = matched_key.tenant
        if not tenant or tenant.status != TenantStatus.active:
            raise HTTPException(status_code=404, detail="Tenant not found")

        if tenant.subscription_expires_at < now:
            raise HTTPException(status_code=403, detail="Subscription expired")
        if fingerprint and not matched_key.fingerprint:
            matched_key.fingerprint = fingerprint

    device = (
        db.query(Device)
        .filter(Device.tenant_id == tenant.id, Device.device_id == payload.device_id)
        .first()
    )
    if device and device.revoked:
        raise HTTPException(status_code=403, detail="Device revoked")

    if not device:
        device = Device(device_id=payload.device_id, tenant_id=tenant.id, last_seen=now)
        db.add(device)
    else:
        device.last_seen = now
    db.flush()

    token, token_data = create_access_token(tenant.id, device_id=payload.device_id, issued_at=now)

    db.add(
        AuditLog(
            tenant_id=tenant.id,
            device_id=device.id,
            action="activate",
            meta={"ip": get_client_ip(request)},
        )
    )
    db.commit()

    return TokenResponse(
        access_token=token,
        issued_at=token_data.issued_at,
        expires_at=token_data.expires_at,
        server_time=now,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, context=Depends(get_request_context)) -> TokenResponse:
    rate_limit_refresh(request)

    if not context.subscription_active:
        raise HTTPException(status_code=403, detail="Subscription expired")

    now = utcnow()
    token, token_data = create_access_token(
        context.tenant.id, device_id=context.token.device_id, issued_at=now
    )

    return TokenResponse(
        access_token=token,
        issued_at=token_data.issued_at,
        expires_at=token_data.expires_at,
        server_time=now,
    )
