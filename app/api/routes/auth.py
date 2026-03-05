import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import (
    get_client_ip,
    get_db,
    get_erp_request_context,
    get_request_context,
    rate_limit_activate,
    rate_limit_refresh,
)
from app.models import (
    AuditLog,
    Device,
    DevicePairingToken,
    ERPUser,
    LicenseKey,
    LicenseKeyStatus,
    OTAAccess,
    Tenant,
    TenantStatus,
)
from app.schemas import (
    ActivateRequest,
    CurrentUserResponse,
    LicenseValidateRequest,
    LicenseValidateResponse,
    PairingActivateRequest,
    TokenResponse,
)
from app.services.auth import create_access_token
from app.services.erp_user_auth import ERPUserAuthError, ERPUserIdentity, authenticate_erp_user
from app.services.erpnext import ERPNextError, request_erpnext
from app.services.erp_user_sync import upsert_erp_user_login
from app.services.license import fingerprint_license_key, verify_license_key_flexible
from app.services.pairing import hash_pairing_token
from app.services.permissions import resolve_app_permissions
from app.utils.time import utcnow

router = APIRouter(tags=["auth"])
logger = logging.getLogger(__name__)


def _resolve_tenant_for_license_validation(
    db: Session,
    raw_key: str,
    company_code: str | None,
    now,
) -> Tenant:
    company_code_norm = company_code.lower() if company_code else None
    fingerprint = fingerprint_license_key(raw_key)

    if company_code_norm:
        tenant = (
            db.query(Tenant)
            .filter(func.lower(Tenant.company_code) == company_code_norm)
            .first()
        )
        if not tenant or tenant.status != TenantStatus.active:
            raise HTTPException(status_code=404, detail="Tenant not found")

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
            raise HTTPException(status_code=401, detail="License key invalid for company code")

        matched_key = next(
            (key for key in active_keys if verify_license_key_flexible(raw_key, key.hashed_key)),
            None,
        )
        if not matched_key:
            raise HTTPException(status_code=401, detail="License key invalid for company code")
        if tenant.subscription_expires_at < now:
            raise HTTPException(status_code=403, detail="Subscription expired")
        return tenant

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
    return tenant


def _tenant_has_active_license(db: Session, tenant_id) -> bool:
    return (
        db.query(LicenseKey.id)
        .filter(
            LicenseKey.tenant_id == tenant_id,
            LicenseKey.status == LicenseKeyStatus.active,
        )
        .first()
        is not None
    )


def _parse_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return True


def _safe_json_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _activate(
    payload: ActivateRequest,
    request: Request,
    db: Session,
    *,
    allow_ota_access: bool,
) -> TokenResponse:
    raw_key = payload.license_key.strip()
    if not raw_key:
        raise HTTPException(status_code=401, detail="License key invalid")

    ota_access = None
    if allow_ota_access:
        ota_access = db.query(OTAAccess).order_by(OTAAccess.id.asc()).first()

    company_code = payload.company_code.strip() if payload.company_code else None
    company_code_norm = company_code.lower() if company_code else None
    rate_limit_key = "ota_access" if ota_access else (company_code_norm or f"license:{raw_key}")
    rate_limit_activate(request, rate_limit_key)

    now = utcnow()
    tenant: Tenant | None = None
    fingerprint = fingerprint_license_key(raw_key)

    if ota_access:
        tenant = db.query(Tenant).filter(Tenant.id == ota_access.tenant_id).first()
        if not tenant or tenant.status != TenantStatus.active:
            raise HTTPException(status_code=404, detail="Tenant not found")

        if tenant.subscription_expires_at < now:
            raise HTTPException(status_code=403, detail="Subscription expired")

        matched_key = (
            db.query(LicenseKey)
            .filter(LicenseKey.id == ota_access.license_key_id)
            .first()
        )
        if not matched_key or matched_key.status != LicenseKeyStatus.active:
            raise HTTPException(status_code=401, detail="License key invalid")

        if not verify_license_key_flexible(raw_key, matched_key.hashed_key):
            raise HTTPException(status_code=401, detail="License key invalid")

        if fingerprint and not matched_key.fingerprint:
            matched_key.fingerprint = fingerprint
    elif company_code_norm:
        tenant = (
            db.query(Tenant)
            .filter(func.lower(Tenant.company_code) == company_code_norm)
            .first()
        )
        if not tenant or tenant.status != TenantStatus.active:
            raise HTTPException(status_code=404, detail="Tenant not found")

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
            raise HTTPException(status_code=401, detail="License key invalid for company code")

        matched_key = next(
            (key for key in active_keys if verify_license_key_flexible(raw_key, key.hashed_key)),
            None,
        )
        if not matched_key:
            raise HTTPException(status_code=401, detail="License key invalid for company code")
        if tenant.subscription_expires_at < now:
            raise HTTPException(status_code=403, detail="Subscription expired")
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

    erp_username = payload.erp_username.strip() if payload.erp_username else None
    erp_password = payload.erp_password if payload.erp_password else None
    if bool(erp_username) != bool(erp_password):
        raise HTTPException(status_code=400, detail="Both erp_username and erp_password are required together")
    if not allow_ota_access and not erp_username:
        raise HTTPException(status_code=400, detail="ERP username and password are required")

    erp_identity = None
    erp_roles: list[str] = []
    full_name: str | None = None
    enabled = True
    if erp_username and erp_password:
        try:
            erp_identity = authenticate_erp_user(tenant.erpnext_url, erp_username, erp_password)
        except ERPUserAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        erp_username = erp_identity.username
        erp_roles = erp_identity.roles
        full_name = erp_identity.full_name
        enabled = erp_identity.enabled

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

    if erp_identity:
        upsert_erp_user_login(
            db=db,
            tenant_id=tenant.id,
            identity=erp_identity,
            now=now,
        )

    app_permissions = sorted(resolve_app_permissions(erp_roles))

    token, token_data = create_access_token(
        tenant.id,
        device_id=payload.device_id,
        erp_username=erp_username,
        erp_roles=erp_roles,
        app_permissions=app_permissions,
        issued_at=now,
    )

    db.add(
        AuditLog(
            tenant_id=tenant.id,
            device_id=device.id,
            action="activate",
            meta={
                "ip": get_client_ip(request),
                "erp_username": erp_username,
                "erp_roles": erp_roles,
                "app_permissions": app_permissions,
                "erp_full_name": full_name,
                "erp_enabled": enabled,
            },
        )
    )
    db.commit()

    return TokenResponse(
        access_token=token,
        issued_at=token_data.issued_at,
        expires_at=token_data.expires_at,
        server_time=now,
    )


@router.post("/activate", response_model=TokenResponse)
def activate(payload: ActivateRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    return _activate(payload, request, db, allow_ota_access=True)


@router.post("/activate-erp", response_model=TokenResponse)
def activate_erp(payload: ActivateRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    return _activate(payload, request, db, allow_ota_access=False)


@router.post("/validate-license", response_model=LicenseValidateResponse)
def validate_license(
    payload: LicenseValidateRequest,
    request: Request,
    db: Session = Depends(get_db),
    ) -> LicenseValidateResponse:
    raw_key = payload.license_key.strip()
    if not raw_key:
        raise HTTPException(status_code=401, detail="License key invalid")

    company_code = payload.company_code.strip() if payload.company_code else None
    rate_limit_key = company_code.lower() if company_code else f"license:{raw_key}"
    rate_limit_activate(request, rate_limit_key)

    now = utcnow()
    tenant = _resolve_tenant_for_license_validation(db, raw_key, company_code, now)
    return LicenseValidateResponse(
        valid=True,
        tenant_id=tenant.id,
        company_code=tenant.company_code,
        server_time=now,
    )


@router.post("/pairing/activate", response_model=TokenResponse)
def activate_pairing(
    payload: PairingActivateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    raw_token = payload.pairing_token.strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="Pairing token invalid")

    rate_limit_activate(request, f"pairing:{payload.device_id}")
    now = utcnow()

    pairing = (
        db.query(DevicePairingToken)
        .filter(DevicePairingToken.token_hash == hash_pairing_token(raw_token))
        .with_for_update()
        .first()
    )
    if not pairing:
        raise HTTPException(status_code=401, detail="Pairing token invalid")
    if pairing.used_at is not None:
        raise HTTPException(status_code=409, detail="Pairing token already used")
    if pairing.expires_at < now:
        raise HTTPException(status_code=401, detail="Pairing token expired")
    if not pairing.enabled:
        raise HTTPException(status_code=403, detail="ERP user disabled")

    tenant = db.query(Tenant).filter(Tenant.id == pairing.tenant_id).first()
    if not tenant or tenant.status != TenantStatus.active:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if tenant.subscription_expires_at < now:
        raise HTTPException(status_code=403, detail="Subscription expired")
    if not _tenant_has_active_license(db, tenant.id):
        raise HTTPException(status_code=403, detail="No active license available")

    identity = ERPUserIdentity(
        username=pairing.erp_username,
        roles=list(pairing.erp_roles or []),
        full_name=pairing.full_name,
        enabled=pairing.enabled,
    )
    upsert_erp_user_login(
        db=db,
        tenant_id=tenant.id,
        identity=identity,
        now=now,
    )

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

    app_permissions = sorted(resolve_app_permissions(identity.roles))
    token, token_data = create_access_token(
        tenant.id,
        device_id=payload.device_id,
        erp_username=identity.username,
        erp_roles=identity.roles,
        app_permissions=app_permissions,
        issued_at=now,
    )

    pairing.used_at = now
    pairing.used_device_id = payload.device_id

    db.add(
        AuditLog(
            tenant_id=tenant.id,
            device_id=device.id,
            action="pairing_activate",
            meta={
                "ip": get_client_ip(request),
                "erp_username": identity.username,
                "erp_roles": identity.roles,
                "app_permissions": app_permissions,
                "pairing_token_id": str(pairing.id),
            },
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

    app_permissions = list(context.token.app_permissions)
    if not app_permissions and context.token.erp_roles:
        app_permissions = sorted(resolve_app_permissions(context.token.erp_roles))

    now = utcnow()
    token, token_data = create_access_token(
        context.tenant.id,
        device_id=context.token.device_id,
        erp_username=context.token.erp_username,
        erp_roles=list(context.token.erp_roles),
        app_permissions=app_permissions,
        issued_at=now,
    )

    return TokenResponse(
        access_token=token,
        issued_at=token_data.issued_at,
        expires_at=token_data.expires_at,
        server_time=now,
    )


@router.get("/me", response_model=CurrentUserResponse)
def me(context=Depends(get_erp_request_context), db: Session = Depends(get_db)) -> CurrentUserResponse:
    username = (context.erp_username or "").strip()
    db_user = (
        db.query(ERPUser)
        .filter(
            ERPUser.tenant_id == context.tenant.id,
            func.lower(ERPUser.erp_username) == username.lower(),
        )
        .first()
    )

    live_full_name: str | None = None
    live_enabled: bool | None = None

    # Live-check ERP user on each /me call so deleted/disabled ERP users are blocked
    # without waiting for a full re-activation cycle.
    if username:
        try:
            safe_username = quote(username, safe="")
            response = request_erpnext(
                context.tenant.erpnext_url,
                context.tenant.api_key,
                context.tenant.api_secret,
                "GET",
                f"/api/resource/User/{safe_username}",
                params={"fields": "[\"name\",\"full_name\",\"enabled\"]"},
            )
            if response.status_code == 404:
                if db_user:
                    db_user.enabled = False
                    db.commit()
                raise HTTPException(status_code=403, detail="ERP user disabled")

            if response.status_code < 400:
                payload = _safe_json_dict(response.json())
                data = _safe_json_dict(payload.get("data"))
                live_enabled = _parse_enabled(data.get("enabled"))
                full_name = data.get("full_name")
                live_full_name = full_name.strip() if isinstance(full_name, str) and full_name.strip() else None

                upsert_erp_user_login(
                    db=db,
                    tenant_id=context.tenant.id,
                    identity=ERPUserIdentity(
                        username=username,
                        roles=list(context.erp_roles),
                        full_name=live_full_name,
                        enabled=live_enabled,
                    ),
                    now=utcnow(),
                )
                db.commit()
                db_user = (
                    db.query(ERPUser)
                    .filter(
                        ERPUser.tenant_id == context.tenant.id,
                        func.lower(ERPUser.erp_username) == username.lower(),
                    )
                    .first()
                )
            elif response.status_code in {401, 403}:
                logger.warning(
                    "ERP user live-check permission denied for tenant=%s user=%s status=%s",
                    context.tenant.id,
                    username,
                    response.status_code,
                )
            else:
                logger.warning(
                    "ERP user live-check skipped for tenant=%s user=%s status=%s",
                    context.tenant.id,
                    username,
                    response.status_code,
                )
        except ERPNextError as exc:
            logger.warning(
                "ERP user live-check failed for tenant=%s user=%s: %s",
                context.tenant.id,
                username,
                exc,
            )
        except ValueError as exc:
            logger.warning(
                "ERP user live-check payload parse failed for tenant=%s user=%s: %s",
                context.tenant.id,
                username,
                exc,
            )

    resolved_enabled = live_enabled if live_enabled is not None else (db_user.enabled if db_user else True)
    if not resolved_enabled:
        raise HTTPException(status_code=403, detail="ERP user disabled")
    resolved_full_name = live_full_name if live_full_name is not None else (db_user.full_name if db_user else None)

    return CurrentUserResponse(
        tenant_id=context.tenant.id,
        company_code=context.tenant.company_code,
        erp_username=username,
        full_name=resolved_full_name,
        enabled=resolved_enabled,
        erp_roles=list(context.erp_roles),
        app_permissions=sorted(context.app_permissions),
    )
