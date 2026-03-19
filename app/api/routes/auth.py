import logging
import re
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.api.deps import (
    RequestContext,
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
    PairingRegisterRequest,
    PairingRegisterResponse,
    ServerCapabilitiesResponse,
    TenantConfigSnapshot,
    TokenResponse,
)
from app.services.auth import create_access_token
from app.services.erp_user_auth import ERPUserAuthError, ERPUserIdentity, authenticate_erp_user
from app.services.erpnext import ERPNextError, request_erpnext
from app.services.erp_user_sync import upsert_erp_user_login
from app.services.license import fingerprint_license_key, verify_license_key_flexible
from app.services.pairing import create_pairing_token, hash_pairing_token
from app.services.permissions import (
    PERMISSION_TENANT_CONFIG_READ,
    PERMISSION_TENANT_CONFIG_WRITE,
    resolve_app_permissions,
)
from app.utils.time import utcnow

router = APIRouter(tags=["auth"])
logger = logging.getLogger(__name__)
settings = get_settings()
PAIRING_SUBDOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _tenant_disabled(company_code: str | None, disabled_list: list[str]) -> bool:
    normalized = str(company_code or "").strip().lower()
    return bool(normalized) and normalized in disabled_list


def _build_server_capabilities(context: RequestContext | None = None) -> ServerCapabilitiesResponse:
    company_code = context.tenant.company_code if context else None
    supports_picklist_process = settings.process_api_enabled and not _tenant_disabled(
        company_code, settings.process_api_disabled_tenants
    )
    supports_picklist_async_completion = (
        supports_picklist_process
        and settings.async_picklist_completion_enabled
        and not _tenant_disabled(company_code, settings.async_picklist_completion_disabled_tenants)
    )
    supports_delivery_note_creation = (
        supports_picklist_process
        and settings.delivery_note_creation_enabled
        and not _tenant_disabled(company_code, settings.delivery_note_creation_disabled_tenants)
    )
    supports_box_count_custom_fields = settings.box_count_custom_fields_enabled and not _tenant_disabled(
        company_code, settings.box_count_custom_fields_disabled_tenants
    )
    return ServerCapabilitiesResponse(
        supports_picklist_process=supports_picklist_process,
        supports_picklist_async_completion=supports_picklist_async_completion,
        supports_delivery_note_creation=supports_delivery_note_creation,
        supports_box_count_custom_fields=supports_box_count_custom_fields,
    )


def _normalize_pairing_subdomain(value: str) -> str:
    text = value.strip().lower()
    if not PAIRING_SUBDOMAIN_RE.fullmatch(text):
        return ""
    return text


def _pairing_domain_suffix() -> str:
    suffix = settings.pairing_domain_suffix.strip()
    if suffix.startswith("https://"):
        suffix = suffix[len("https://"):]
    elif suffix.startswith("http://"):
        suffix = suffix[len("http://"):]
    return suffix.strip().lstrip(".").rstrip("/")


def _build_pairing_erp_url(subdomain: str) -> str:
    return f"https://{subdomain}.{_pairing_domain_suffix()}"


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


def _default_tenant_config_payload() -> dict[str, Any]:
    return {
        "access": {
            "client_profile": "STANDARD",
            "pick_list_images_enabled": False,
            "erp_custom_fields_enabled": False,
            "box_count_feature_enabled": False,
            "or_qty_label": None,
            "or_qty_required": True,
        },
        "barcodes": [],
        "scales": {
            "scale_enabled": False,
            "scale_unit": "kg",
            "hosts": [],
        },
    }


def _sanitize_tenant_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    sanitized.pop("fulfillment_rules", None)
    return sanitized


def _tenant_config_permissions_declared(context: RequestContext) -> bool:
    return bool(context.app_permissions)


def _can_write_tenant_config(context: RequestContext) -> bool:
    if PERMISSION_TENANT_CONFIG_WRITE in context.app_permissions:
        return True
    return not _tenant_config_permissions_declared(context)


def _can_read_tenant_config(context: RequestContext) -> bool:
    if (
        PERMISSION_TENANT_CONFIG_READ in context.app_permissions
        or PERMISSION_TENANT_CONFIG_WRITE in context.app_permissions
    ):
        return True
    return not _tenant_config_permissions_declared(context)


def _serialize_tenant_config_snapshot(tenant: Tenant) -> TenantConfigSnapshot:
    payload = _sanitize_tenant_config_payload(_safe_json_dict(tenant.tenant_config))
    merged_payload = _default_tenant_config_payload()
    merged_payload.update(payload)
    if isinstance(payload.get("access"), dict):
        merged_payload["access"] = {**merged_payload["access"], **payload["access"]}
    if isinstance(payload.get("scales"), dict):
        merged_payload["scales"] = {**merged_payload["scales"], **payload["scales"]}
    if isinstance(payload.get("barcodes"), list):
        merged_payload["barcodes"] = payload["barcodes"]

    return TenantConfigSnapshot(
        config_revision=(tenant.tenant_config_revision or "").strip(),
        settings_scope="tenant",
        updated_at=tenant.tenant_config_updated_at.isoformat() if tenant.tenant_config_updated_at else None,
        updated_by=(tenant.tenant_config_updated_by or "").strip() or None,
        payload=merged_payload,
    )


def _resolve_tenant_id_by_company_code(db: Session, company_code: str | None) -> uuid.UUID | None:
    if not company_code:
        return None
    normalized = company_code.strip().lower()
    if not normalized:
        return None
    row = (
        db.query(Tenant.id)
        .filter(func.lower(Tenant.company_code) == normalized)
        .first()
    )
    return row[0] if row else None


def _resolve_tenant_id_by_pairing_token(db: Session, raw_token: str | None) -> uuid.UUID | None:
    if not raw_token:
        return None
    row = (
        db.query(DevicePairingToken.tenant_id)
        .filter(DevicePairingToken.token_hash == hash_pairing_token(raw_token))
        .first()
    )
    return row[0] if row else None


def _log_auth_failure(
    db: Session,
    request: Request,
    *,
    action: str,
    status_code: int,
    detail: str,
    tenant_id: uuid.UUID | None = None,
    attempted_company_code: str | None = None,
    attempted_device_id: str | None = None,
    attempted_erp_username: str | None = None,
    key_fingerprint: str | None = None,
    pairing_token_hash_prefix: str | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> None:
    meta: dict[str, Any] = {
        "ip": get_client_ip(request),
        "status_code": status_code,
        "detail": detail,
        "attempted_company_code": attempted_company_code,
        "attempted_device_id": attempted_device_id,
        "attempted_erp_username": attempted_erp_username,
        "key_fingerprint": key_fingerprint,
        "pairing_token_hash_prefix": pairing_token_hash_prefix,
    }
    if extra_meta:
        meta.update(extra_meta)

    try:
        db.rollback()
        db.add(
            AuditLog(
                tenant_id=tenant_id,
                device_id=None,
                action=action,
                meta=meta,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to write auth failure audit event: %s", action)


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

        if tenant.subscription_expires_at < now and not tenant.is_system:
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
        erp_url=tenant.erpnext_url,
    )


@router.post("/activate", response_model=TokenResponse)
def activate(payload: ActivateRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        return _activate(payload, request, db, allow_ota_access=True)
    except HTTPException as exc:
        company_code = payload.company_code.strip() if payload.company_code else None
        _log_auth_failure(
            db=db,
            request=request,
            action="activate_failed",
            status_code=exc.status_code,
            detail=str(exc.detail),
            tenant_id=_resolve_tenant_id_by_company_code(db, company_code),
            attempted_company_code=company_code,
            attempted_device_id=payload.device_id,
            attempted_erp_username=(payload.erp_username.strip() if payload.erp_username else None),
            key_fingerprint=fingerprint_license_key(payload.license_key.strip()) or None,
            extra_meta={"allow_ota_access": True},
        )
        raise


@router.post("/activate-erp", response_model=TokenResponse)
def activate_erp(payload: ActivateRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        return _activate(payload, request, db, allow_ota_access=False)
    except HTTPException as exc:
        company_code = payload.company_code.strip() if payload.company_code else None
        _log_auth_failure(
            db=db,
            request=request,
            action="activate_erp_failed",
            status_code=exc.status_code,
            detail=str(exc.detail),
            tenant_id=_resolve_tenant_id_by_company_code(db, company_code),
            attempted_company_code=company_code,
            attempted_device_id=payload.device_id,
            attempted_erp_username=(payload.erp_username.strip() if payload.erp_username else None),
            key_fingerprint=fingerprint_license_key(payload.license_key.strip()) or None,
            extra_meta={"allow_ota_access": False},
        )
        raise


@router.post("/validate-license", response_model=LicenseValidateResponse)
def validate_license(
    payload: LicenseValidateRequest,
    request: Request,
    db: Session = Depends(get_db),
    ) -> LicenseValidateResponse:
    raw_key = payload.license_key.strip()
    company_code = payload.company_code.strip() if payload.company_code else None
    try:
        if not raw_key:
            raise HTTPException(status_code=401, detail="License key invalid")

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
    except HTTPException as exc:
        _log_auth_failure(
            db=db,
            request=request,
            action="validate_license_failed",
            status_code=exc.status_code,
            detail=str(exc.detail),
            tenant_id=_resolve_tenant_id_by_company_code(db, company_code),
            attempted_company_code=company_code,
            key_fingerprint=fingerprint_license_key(raw_key) or None,
        )
        raise


@router.post("/pairing/activate", response_model=TokenResponse)
def activate_pairing(
    payload: PairingActivateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    raw_token = payload.pairing_token.strip()
    try:
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
            erp_url=tenant.erpnext_url,
        )
    except HTTPException as exc:
        token_hash = hash_pairing_token(raw_token) if raw_token else None
        _log_auth_failure(
            db=db,
            request=request,
            action="pairing_activate_failed",
            status_code=exc.status_code,
            detail=str(exc.detail),
            tenant_id=_resolve_tenant_id_by_pairing_token(db, raw_token),
            attempted_device_id=payload.device_id,
            pairing_token_hash_prefix=(token_hash[:12] if token_hash else None),
        )
        raise


@router.post("/pairing/register", response_model=PairingRegisterResponse)
def register_pairing(
    payload: PairingRegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> PairingRegisterResponse:
    normalized_subdomain = _normalize_pairing_subdomain(payload.subdomain)
    normalized_username = payload.erp_username.strip()
    raw_password = payload.erp_password
    try:
        if not normalized_subdomain:
            raise HTTPException(status_code=400, detail="Invalid company code. Use letters, numbers, and hyphen.")
        if not normalized_username or not raw_password:
            raise HTTPException(status_code=400, detail="ERP username and password are required.")

        rate_limit_activate(request, f"pairing-register:{normalized_subdomain}")
        now = utcnow()
        target_url = _build_pairing_erp_url(normalized_subdomain)

        tenant = (
            db.query(Tenant)
            .filter(func.lower(Tenant.erpnext_url) == target_url.lower())
            .first()
        )
        if not tenant or tenant.status != TenantStatus.active:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        if tenant.subscription_expires_at < now:
            raise HTTPException(status_code=403, detail="Subscription expired.")
        if not _tenant_has_active_license(db, tenant.id):
            raise HTTPException(status_code=403, detail="No active license for this tenant.")

        try:
            identity = authenticate_erp_user(tenant.erpnext_url, normalized_username, raw_password)
        except ERPUserAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        if not identity.enabled:
            raise HTTPException(status_code=403, detail="ERP user disabled.")

        expires_at = now + timedelta(minutes=max(settings.pairing_token_ttl_minutes, 1))
        pairing_token = create_pairing_token(
            db=db,
            tenant_id=tenant.id,
            identity=identity,
            expires_at=expires_at,
            created_ip=get_client_ip(request),
        )
        upsert_erp_user_login(db=db, tenant_id=tenant.id, identity=identity, now=now)
        db.add(
            AuditLog(
                tenant_id=tenant.id,
                device_id=None,
                action="pairing_register",
                meta={
                    "ip": get_client_ip(request),
                    "erp_username": identity.username,
                    "erp_roles": identity.roles,
                    "subdomain": normalized_subdomain,
                },
            )
        )
        db.commit()

        return PairingRegisterResponse(
            pairing_token=pairing_token,
            expires_at=expires_at,
            server_time=now,
            erp_url=tenant.erpnext_url,
        )
    except HTTPException as exc:
        _log_auth_failure(
            db=db,
            request=request,
            action="pairing_register_failed",
            status_code=exc.status_code,
            detail=str(exc.detail),
            attempted_company_code=normalized_subdomain or None,
            attempted_erp_username=normalized_username or None,
        )
        raise


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, context=Depends(get_request_context)) -> TokenResponse:
    rate_limit_refresh(request)

    if not context.subscription_active:
        raise HTTPException(status_code=403, detail="Subscription expired")

    app_permissions = sorted(resolve_app_permissions(context.token.erp_roles))
    if not app_permissions:
        app_permissions = list(context.token.app_permissions)

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
        erp_url=context.tenant.erpnext_url,
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
        capabilities=_build_server_capabilities(context),
        tenant_config_snapshot=(
            _serialize_tenant_config_snapshot(context.tenant)
            if _can_read_tenant_config(context)
            else None
        ),
    )


@router.get("/tenant-config", response_model=TenantConfigSnapshot)
def get_tenant_config(context=Depends(get_erp_request_context)) -> TenantConfigSnapshot:
    if not _can_read_tenant_config(context):
        raise HTTPException(status_code=403, detail="Permission denied: tenant_config.read")
    return _serialize_tenant_config_snapshot(context.tenant)


@router.put("/tenant-config", response_model=TenantConfigSnapshot)
def put_tenant_config(
    payload: TenantConfigSnapshot,
    context=Depends(get_erp_request_context),
    db: Session = Depends(get_db),
) -> TenantConfigSnapshot:
    if not _can_write_tenant_config(context):
        raise HTTPException(status_code=403, detail="Permission denied: tenant_config.write")

    current_revision = (context.tenant.tenant_config_revision or "").strip()
    incoming_revision = payload.config_revision.strip()
    if current_revision and incoming_revision and incoming_revision != current_revision:
        raise HTTPException(status_code=409, detail="Tenant config revision mismatch")

    now = utcnow()
    next_revision = uuid.uuid4().hex
    context.tenant.tenant_config = _sanitize_tenant_config_payload(payload.payload.model_dump(mode="python"))
    context.tenant.tenant_config_revision = next_revision
    context.tenant.tenant_config_updated_by = (context.erp_username or "").strip() or None
    context.tenant.tenant_config_updated_at = now
    db.add(context.tenant)
    db.commit()
    db.refresh(context.tenant)
    return _serialize_tenant_config_snapshot(context.tenant)
