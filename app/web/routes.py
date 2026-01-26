import hmac
import secrets
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, get_db
from app.config import get_settings
from app.models import (
    AuditLog,
    Device,
    ERPAllowlistEntry,
    ERPAllowlistType,
    LicenseKey,
    LicenseKeyStatus,
    OTAAccess,
    Tenant,
    TenantStatus,
)
from app.models.firmware import DeviceOTALog, Firmware
from app.services.allowlist import (
    has_allowlist_entries,
    normalize_doctype,
    normalize_method,
    seed_allowlist_from_settings,
)
from app.services.rate_limit import RateLimiter
from app.services.erpnext import normalize_erpnext_url
from app.services.license import fingerprint_license_key, hash_license_key
from app.services.ota import OTAService
from app.utils.time import utcnow

router = APIRouter(prefix="/admin-ui", tags=["admin-ui"])
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()
login_limiter = RateLimiter(settings.rate_limit_login_per_minute, 60)
ota_service = OTAService(firmware_base_path="firmware")


def parse_datetime_input(value: str) -> datetime:
    if "T" not in value:
        parsed_date = date.fromisoformat(value)
        parsed = datetime.combine(parsed_date, time(23, 59, 59))
    else:
        parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_tenant_status(value: str) -> TenantStatus | None:
    try:
        return TenantStatus(value)
    except ValueError:
        return None


def parse_license_status(value: str) -> LicenseKeyStatus | None:
    try:
        return LicenseKeyStatus(value)
    except ValueError:
        return None


def is_admin(request: Request) -> bool:
    return bool(request.session.get("is_admin"))


def validate_admin_session(request: Request) -> bool:
    if not is_admin(request):
        return False
    now_ts = int(utcnow().timestamp())
    login_at = request.session.get("login_at")
    last_seen = request.session.get("last_seen")
    max_age = settings.admin_session_max_age_seconds
    idle = settings.admin_session_idle_seconds

    try:
        login_at = int(login_at)
        last_seen = int(last_seen)
    except (TypeError, ValueError):
        request.session.clear()
        return False

    if max_age > 0 and now_ts - login_at > max_age:
        request.session.clear()
        set_flash(request, error="Admin session expired. Please log in again.")
        return False
    if idle > 0 and now_ts - last_seen > idle:
        request.session.clear()
        set_flash(request, error="Admin session timed out. Please log in again.")
        return False

    request.session["last_seen"] = now_ts
    return True


def require_admin_or_redirect(request: Request):
    if not validate_admin_session(request):
        return RedirectResponse("/admin-ui/login", status_code=303)
    return None


def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def require_csrf(request: Request, form: dict, redirect_path: str) -> RedirectResponse | None:
    token = str(form.get("csrf_token") or "").strip()
    if not token or token != request.session.get("csrf_token"):
        set_flash(request, error="Invalid CSRF token")
        return redirect_to(redirect_path)
    return None


def set_flash(request: Request, message: str | None = None, error: str | None = None, license_key: str | None = None):
    if message:
        request.session["flash_message"] = message
    if error:
        request.session["flash_error"] = error
    if license_key:
        request.session["flash_license_key"] = license_key


def pop_flash(request: Request) -> tuple[str | None, str | None, str | None]:
    message = request.session.pop("flash_message", None)
    error = request.session.pop("flash_error", None)
    license_key = request.session.pop("flash_license_key", None)
    return message, error, license_key


def redirect_to(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def get_tenant_or_none(db: Session, company_code: str) -> Tenant | None:
    return db.query(Tenant).filter(Tenant.company_code == company_code).first()


def build_admin_context(
    request: Request,
    title: str,
    active_context: str,
    active_page: str,
    **extra,
) -> dict:
    context = {
        "request": request,
        "title": title,
        "is_admin": True,
        "active_context": active_context,
        "active_page": active_page,
    }
    context.update(extra)
    return context


@router.get("/")
def index() -> RedirectResponse:
    return redirect_to("/admin-ui/licensing")


@router.get("/login")
def login_page(request: Request):
    if validate_admin_session(request):
        return redirect_to("/admin-ui/licensing")
    message, error, _ = pop_flash(request)
    context = {
        "request": request,
        "title": "Admin Login",
        "message": message,
        "error": error,
        "admin_token_configured": bool(settings.admin_token),
        "is_admin": False,
        "active_context": None,
        "active_page": None,
        "csrf_token": get_csrf_token(request),
    }
    return templates.TemplateResponse("login.html", context)


@router.post("/login")
async def login(request: Request):
    if not settings.admin_token:
        set_flash(request, error="ADMIN_TOKEN is not configured")
        return redirect_to("/admin-ui/login")

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/login")
    if csrf_error:
        return csrf_error

    client_ip = get_client_ip(request)
    if not login_limiter.allow(client_ip):
        set_flash(request, error="Too many login attempts. Try again later.")
        return redirect_to("/admin-ui/login")

    token = str(form.get("admin_token") or "")
    if not hmac.compare_digest(token, settings.admin_token):
        set_flash(request, error="Invalid admin token")
        return redirect_to("/admin-ui/login")

    request.session["is_admin"] = True
    now_ts = int(utcnow().timestamp())
    request.session["login_at"] = now_ts
    request.session["last_seen"] = now_ts
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    set_flash(request, message="Logged in")
    return redirect_to("/admin-ui/licensing")


@router.get("/licensing")
def licensing_dashboard(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    total_tenants = db.query(Tenant).count()
    active_tenants = db.query(Tenant).filter(Tenant.status == TenantStatus.active).count()
    suspended_tenants = db.query(Tenant).filter(Tenant.status == TenantStatus.suspended).count()
    total_licenses = db.query(LicenseKey).count()
    active_licenses = db.query(LicenseKey).filter(LicenseKey.status == LicenseKeyStatus.active).count()
    revoked_licenses = db.query(LicenseKey).filter(LicenseKey.status == LicenseKeyStatus.revoked).count()

    soon_date = utcnow() + timedelta(days=30)
    expiring_soon = (
        db.query(Tenant)
        .filter(Tenant.subscription_expires_at <= soon_date)
        .count()
    )

    context = build_admin_context(
        request,
        "Licensing Dashboard",
        "licensing",
        "licensing-dashboard",
        total_tenants=total_tenants,
        active_tenants=active_tenants,
        suspended_tenants=suspended_tenants,
        total_licenses=total_licenses,
        active_licenses=active_licenses,
        revoked_licenses=revoked_licenses,
        expiring_soon=expiring_soon,
    )
    return templates.TemplateResponse("licensing_dashboard.html", context)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect_to("/admin-ui/login")


@router.get("/tenants")
def list_tenants(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenants = db.query(Tenant).order_by(Tenant.company_code.asc()).all()
    message, error, _ = pop_flash(request)

    context = build_admin_context(
        request,
        "Tenants",
        "licensing",
        "tenants",
        tenants=tenants,
        message=message,
        error=error,
        statuses=[status.value for status in TenantStatus],
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("tenants.html", context)


@router.post("/tenants")
async def create_tenant(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/tenants")
    if csrf_error:
        return csrf_error

    company_code = str(form.get("company_code") or "").strip()
    company_name = str(form.get("company_name") or "").strip()
    erpnext_url = normalize_erpnext_url(str(form.get("erpnext_url") or ""))
    api_key = str(form.get("api_key") or "").strip()
    api_secret = str(form.get("api_secret") or "").strip()
    expires_at_raw = str(form.get("subscription_expires_at") or "").strip()
    status_raw = str(form.get("status") or TenantStatus.active.value).strip()

    if not company_code or not erpnext_url or not api_key or not api_secret or not expires_at_raw:
        set_flash(request, error="All fields are required")
        return redirect_to("/admin-ui/tenants")

    if get_tenant_or_none(db, company_code):
        set_flash(request, error="Tenant already exists")
        return redirect_to("/admin-ui/tenants")

    status = parse_tenant_status(status_raw)
    if not status:
        set_flash(request, error="Invalid status")
        return redirect_to("/admin-ui/tenants")

    try:
        expires_at = parse_datetime_input(expires_at_raw)
    except ValueError:
        set_flash(request, error="Invalid date format")
        return redirect_to("/admin-ui/tenants")

    tenant = Tenant(
        company_code=company_code,
        company_name=company_name or None,
        erpnext_url=erpnext_url,
        api_key=api_key,
        api_secret=api_secret,
        status=status,
        subscription_expires_at=expires_at,
    )
    db.add(tenant)
    db.commit()

    set_flash(request, message="Tenant created")
    return redirect_to("/admin-ui/tenants")


@router.get("/tenants/{company_code}")
def tenant_detail(request: Request, company_code: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

    licenses = (
        db.query(LicenseKey)
        .filter(LicenseKey.tenant_id == tenant.id)
        .order_by(LicenseKey.created_at.desc())
        .all()
    )
    message, error, license_key = pop_flash(request)

    context = build_admin_context(
        request,
        f"Tenant {tenant.company_code}",
        "licensing",
        "tenants",
        tenant=tenant,
        licenses=licenses,
        message=message,
        error=error,
        new_license_key=license_key,
        tenant_statuses=[status.value for status in TenantStatus],
        license_statuses=[status.value for status in LicenseKeyStatus],
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("tenant_detail.html", context)


@router.get("/licenses")
def list_licenses(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    licenses = (
        db.query(LicenseKey)
        .join(Tenant, LicenseKey.tenant_id == Tenant.id)
        .order_by(LicenseKey.created_at.desc())
        .all()
    )

    context = build_admin_context(
        request,
        "Licenses",
        "licensing",
        "licenses",
        licenses=licenses,
    )
    return templates.TemplateResponse("licenses.html", context)


@router.post("/tenants/{company_code}/status")
async def update_tenant_status(request: Request, company_code: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

    form = await request.form()
    csrf_error = require_csrf(request, form, f"/admin-ui/tenants/{company_code}")
    if csrf_error:
        return csrf_error

    status_raw = str(form.get("status") or "").strip()
    status = parse_tenant_status(status_raw)
    if not status:
        set_flash(request, error="Invalid status")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    tenant.status = status
    db.commit()
    set_flash(request, message="Status updated")
    return redirect_to(f"/admin-ui/tenants/{company_code}")


@router.post("/tenants/{company_code}/subscription")
async def update_subscription(request: Request, company_code: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

    form = await request.form()
    csrf_error = require_csrf(request, form, f"/admin-ui/tenants/{company_code}")
    if csrf_error:
        return csrf_error

    expires_at_raw = str(form.get("expires_at") or "").strip()
    add_days_raw = str(form.get("add_days") or "").strip()

    if expires_at_raw and add_days_raw:
        set_flash(request, error="Use expires_at or add_days, not both")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    if not expires_at_raw and not add_days_raw:
        set_flash(request, error="Provide expires_at or add_days")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    if expires_at_raw:
        try:
            tenant.subscription_expires_at = parse_datetime_input(expires_at_raw)
        except ValueError:
            set_flash(request, error="Invalid date format")
            return redirect_to(f"/admin-ui/tenants/{company_code}")
    else:
        try:
            add_days = int(add_days_raw)
        except ValueError:
            set_flash(request, error="Invalid add_days")
            return redirect_to(f"/admin-ui/tenants/{company_code}")
        if add_days < 1:
            set_flash(request, error="add_days must be >= 1")
            return redirect_to(f"/admin-ui/tenants/{company_code}")

        now = utcnow()
        base = tenant.subscription_expires_at
        if base < now:
            base = now
        tenant.subscription_expires_at = base + timedelta(days=add_days)

    db.commit()
    set_flash(request, message="Subscription updated")
    return redirect_to(f"/admin-ui/tenants/{company_code}")


@router.post("/tenants/{company_code}/delete")
async def delete_tenant(request: Request, company_code: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, f"/admin-ui/tenants/{company_code}")
    if csrf_error:
        return csrf_error

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

    device_ids = [
        row.id for row in db.query(Device.id).filter(Device.tenant_id == tenant.id).all()
    ]
    audit_query = db.query(AuditLog)
    if device_ids:
        audit_query = audit_query.filter(
            (AuditLog.tenant_id == tenant.id) | (AuditLog.device_id.in_(device_ids))
        )
    else:
        audit_query = audit_query.filter(AuditLog.tenant_id == tenant.id)
    audit_query.delete(synchronize_session=False)

    db.delete(tenant)
    db.commit()
    set_flash(request, message="Tenant deleted")
    return redirect_to("/admin-ui/tenants")


@router.post("/tenants/{company_code}/licenses")
async def create_license(request: Request, company_code: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

    form = await request.form()
    csrf_error = require_csrf(request, form, f"/admin-ui/tenants/{company_code}")
    if csrf_error:
        return csrf_error

    license_key_raw = str(form.get("license_key") or "").strip()
    status_raw = str(form.get("status") or LicenseKeyStatus.active.value).strip()

    status = parse_license_status(status_raw)
    if not status:
        set_flash(request, error="Invalid license status")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    license_key = license_key_raw or secrets.token_urlsafe(32)
    fingerprint = fingerprint_license_key(license_key) or None
    if fingerprint:
        existing = (
            db.query(LicenseKey)
            .filter(LicenseKey.fingerprint == fingerprint)
            .first()
        )
        if existing:
            set_flash(request, error="License key already exists")
            return redirect_to(f"/admin-ui/tenants/{company_code}")
    license_entry = LicenseKey(
        tenant_id=tenant.id,
        hashed_key=hash_license_key(license_key),
        fingerprint=fingerprint,
        status=status,
    )
    db.add(license_entry)
    db.commit()

    set_flash(request, message="License created", license_key=license_key)
    return redirect_to(f"/admin-ui/tenants/{company_code}")


@router.post("/licenses/{license_id}/status")
async def update_license_status(request: Request, license_id: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    company_code = str(form.get("company_code") or "").strip()
    status_raw = str(form.get("status") or "").strip()
    redirect_target = f"/admin-ui/tenants/{company_code}" if company_code else "/admin-ui/tenants"
    csrf_error = require_csrf(request, form, redirect_target)
    if csrf_error:
        return csrf_error

    status = parse_license_status(status_raw)
    if not status:
        set_flash(request, error="Invalid license status")
        return redirect_to(redirect_target)

    try:
        license_uuid = uuid.UUID(license_id)
    except ValueError:
        set_flash(request, error="Invalid license id")
        return redirect_to(redirect_target)

    license_entry = db.query(LicenseKey).filter(LicenseKey.id == license_uuid).first()
    if not license_entry:
        set_flash(request, error="License not found")
        return redirect_to(redirect_target)

    license_entry.status = status
    db.commit()

    set_flash(request, message="License status updated")
    return redirect_to(redirect_target)


@router.post("/licenses/{license_id}/delete")
async def delete_license(request: Request, license_id: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    company_code = str(form.get("company_code") or "").strip()
    redirect_target = f"/admin-ui/tenants/{company_code}" if company_code else "/admin-ui/tenants"
    csrf_error = require_csrf(request, form, redirect_target)
    if csrf_error:
        return csrf_error

    try:
        license_uuid = uuid.UUID(license_id)
    except ValueError:
        set_flash(request, error="Invalid license id")
        return redirect_to(redirect_target)

    license_entry = db.query(LicenseKey).filter(LicenseKey.id == license_uuid).first()
    if not license_entry:
        set_flash(request, error="License not found")
        return redirect_to(redirect_target)

    db.delete(license_entry)
    db.commit()
    set_flash(request, message="License deleted")
    return redirect_to(redirect_target)


@router.get("/ota")
def ota_dashboard(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    message, error, _ = pop_flash(request)
    total_firmwares = db.query(Firmware).count()
    active_firmwares = db.query(Firmware).filter(Firmware.is_active == True).count()
    stable_firmwares = db.query(Firmware).filter(Firmware.is_stable == True).count()
    total_logs = db.query(DeviceOTALog).count()
    failed_logs = db.query(DeviceOTALog).filter(DeviceOTALog.status == "failed").count()

    context = build_admin_context(
        request,
        "OTA Dashboard",
        "ota",
        "ota-dashboard",
        total_firmwares=total_firmwares,
        active_firmwares=active_firmwares,
        stable_firmwares=stable_firmwares,
        total_logs=total_logs,
        failed_logs=failed_logs,
        message=message,
        error=error,
    )
    return templates.TemplateResponse("ota_dashboard.html", context)


@router.get("/ota/releases")
def ota_releases(request: Request, db: Session = Depends(get_db), device_type: str | None = None):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    message, error, _ = pop_flash(request)
    query = db.query(Firmware)
    if device_type:
        query = query.filter(Firmware.device_type == device_type)
    firmwares = (
        query.order_by(Firmware.device_type.asc(), Firmware.version.desc(), Firmware.build_number.desc())
        .all()
    )
    device_types = [
        row[0] for row in db.query(Firmware.device_type).distinct().order_by(Firmware.device_type.asc()).all()
    ]

    context = build_admin_context(
        request,
        "OTA Releases",
        "ota",
        "ota-releases",
        firmwares=firmwares,
        device_types=device_types,
        selected_device_type=device_type or "",
        message=message,
        error=error,
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("ota_releases.html", context)


@router.post("/ota/releases")
async def create_ota_release(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/ota/releases")
    if csrf_error:
        return csrf_error

    device_type = str(form.get("device_type") or "").strip()
    version = str(form.get("version") or "").strip()
    build_raw = str(form.get("build_number") or "").strip()
    description = str(form.get("description") or "").strip() or None
    release_notes = str(form.get("release_notes") or "").strip() or None
    min_current_version = str(form.get("min_current_version") or "").strip() or None
    is_stable = bool(form.get("is_stable"))

    upload = form.get("firmware_file")
    if not device_type or not version or not build_raw or not upload:
        set_flash(request, error="Device type, version, build number, and file are required")
        return redirect_to("/admin-ui/ota/releases")

    try:
        build_number = int(build_raw)
        if build_number < 1:
            raise ValueError
    except ValueError:
        set_flash(request, error="Build number must be a positive integer")
        return redirect_to("/admin-ui/ota/releases")

    if ota_service._parse_version(version) is None:
        set_flash(request, error="Version must be semantic (e.g., 1.2.3)")
        return redirect_to("/admin-ui/ota/releases")

    if min_current_version and ota_service._parse_version(min_current_version) is None:
        set_flash(request, error="Min current version must be semantic (e.g., 1.0.0)")
        return redirect_to("/admin-ui/ota/releases")

    existing = (
        db.query(Firmware)
        .filter(
            Firmware.device_type == device_type,
            Firmware.version == version,
            Firmware.build_number == build_number,
        )
        .first()
    )
    if existing:
        set_flash(request, error="Firmware with same device type, version, and build already exists")
        return redirect_to("/admin-ui/ota/releases")

    raw_filename = getattr(upload, "filename", None) or f"{device_type}-v{version}-b{build_number}.bin"
    filename = str(raw_filename).split("/")[-1].split("\\")[-1]
    safe_device_type = "".join(ch for ch in device_type if ch.isalnum() or ch in ("-", "_")).strip()
    if not safe_device_type:
        set_flash(request, error="Device type contains invalid characters")
        return redirect_to("/admin-ui/ota/releases")

    binary_path = f"{safe_device_type}/v{version}_b{build_number}.bin"
    file_path = ota_service.firmware_path / binary_path
    file_path.parent.mkdir(parents=True, exist_ok=True)

    file_bytes = await upload.read()
    if not file_bytes:
        set_flash(request, error="Uploaded file is empty")
        return redirect_to("/admin-ui/ota/releases")

    file_path.write_bytes(file_bytes)
    file_hash = ota_service.calculate_file_hash(file_path)
    file_size = file_path.stat().st_size

    firmware = Firmware(
        device_type=device_type,
        version=version,
        build_number=build_number,
        filename=filename,
        file_size=file_size,
        file_hash=file_hash,
        binary_path=binary_path,
        description=description,
        release_notes=release_notes,
        is_stable=is_stable,
        min_current_version=min_current_version,
        is_active=True,
        released_at=utcnow() if is_stable else None,
    )
    db.add(firmware)
    db.commit()
    set_flash(request, message="Firmware uploaded and registered")
    return redirect_to("/admin-ui/ota/releases")


@router.get("/ota/access")
def ota_access(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    message, error, _ = pop_flash(request)
    current_setting = db.query(OTAAccess).order_by(OTAAccess.id.asc()).first()
    tenants = db.query(Tenant).order_by(Tenant.company_code.asc()).all()
    license_keys = (
        db.query(LicenseKey)
        .join(Tenant, LicenseKey.tenant_id == Tenant.id)
        .order_by(Tenant.company_code.asc(), LicenseKey.created_at.desc())
        .all()
    )

    context = build_admin_context(
        request,
        "ESP32 Access",
        "ota",
        "ota-access",
        tenants=tenants,
        license_keys=license_keys,
        current_setting=current_setting,
        current_tenant_id=current_setting.tenant_id if current_setting else None,
        current_license_id=current_setting.license_key_id if current_setting else None,
        message=message,
        error=error,
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("ota_access.html", context)


@router.post("/ota/access")
async def update_ota_access(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/ota/access")
    if csrf_error:
        return csrf_error

    tenant_raw = str(form.get("tenant_id") or "").strip()
    license_raw = str(form.get("license_key_id") or "").strip()
    if not tenant_raw or not license_raw:
        set_flash(request, error="Select both tenant and license key")
        return redirect_to("/admin-ui/ota/access")

    try:
        tenant_id = uuid.UUID(tenant_raw)
        license_id = uuid.UUID(license_raw)
    except ValueError:
        set_flash(request, error="Invalid tenant or license id")
        return redirect_to("/admin-ui/ota/access")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/ota/access")

    license_key = db.query(LicenseKey).filter(LicenseKey.id == license_id).first()
    if not license_key:
        set_flash(request, error="License key not found")
        return redirect_to("/admin-ui/ota/access")

    if license_key.tenant_id != tenant.id:
        set_flash(request, error="License key does not belong to selected tenant")
        return redirect_to("/admin-ui/ota/access")

    current_setting = db.query(OTAAccess).order_by(OTAAccess.id.asc()).first()
    if current_setting:
        current_setting.tenant_id = tenant.id
        current_setting.license_key_id = license_key.id
    else:
        current_setting = OTAAccess(tenant_id=tenant.id, license_key_id=license_key.id)
        db.add(current_setting)

    db.commit()
    set_flash(request, message="ESP32 access updated")
    return redirect_to("/admin-ui/ota/access")


@router.post("/ota/access/clear")
async def clear_ota_access(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/ota/access")
    if csrf_error:
        return csrf_error

    current_setting = db.query(OTAAccess).order_by(OTAAccess.id.asc()).first()
    if current_setting:
        db.delete(current_setting)
        db.commit()
        set_flash(request, message="ESP32 access cleared")
    return redirect_to("/admin-ui/ota/access")


@router.post("/ota/firmware/{firmware_id}/update")
async def update_ota_release(request: Request, firmware_id: int, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/ota/releases")
    if csrf_error:
        return csrf_error

    firmware = db.query(Firmware).filter(Firmware.id == firmware_id).first()
    if not firmware:
        set_flash(request, error="Firmware not found")
        return redirect_to("/admin-ui/ota/releases")

    min_current_version = str(form.get("min_current_version") or "").strip() or None
    if min_current_version and ota_service._parse_version(min_current_version) is None:
        set_flash(request, error="Min current version must be semantic (e.g., 1.0.0)")
        return redirect_to("/admin-ui/ota/releases")

    firmware.is_stable = bool(form.get("is_stable"))
    firmware.is_active = bool(form.get("is_active"))
    firmware.min_current_version = min_current_version
    if firmware.is_stable and firmware.released_at is None:
        firmware.released_at = utcnow()

    db.commit()
    set_flash(request, message="Firmware updated")
    return redirect_to("/admin-ui/ota/releases")


@router.post("/ota/firmware/{firmware_id}/delete")
async def delete_ota_release(request: Request, firmware_id: int, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/ota/releases")
    if csrf_error:
        return csrf_error

    firmware = db.query(Firmware).filter(Firmware.id == firmware_id).first()
    if not firmware:
        set_flash(request, error="Firmware not found")
        return redirect_to("/admin-ui/ota/releases")

    binary_path = ota_service.get_firmware_binary_path(firmware)
    try:
        if binary_path.exists():
            binary_path.unlink()
    except OSError:
        set_flash(request, error="Firmware file could not be deleted")
        return redirect_to("/admin-ui/ota/releases")

    db.delete(firmware)
    db.commit()
    set_flash(request, message="Firmware deleted")
    return redirect_to("/admin-ui/ota/releases")


@router.get("/ota/devices")
def ota_devices(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    logs = (
        db.query(DeviceOTALog)
        .order_by(DeviceOTALog.updated_at.desc())
        .limit(50)
        .all()
    )

    context = build_admin_context(
        request,
        "OTA Devices",
        "ota",
        "ota-devices",
        logs=logs,
    )
    return templates.TemplateResponse("ota_devices.html", context)


@router.get("/ota/channels")
def ota_channels(request: Request):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    context = build_admin_context(
        request,
        "OTA Channels",
        "ota",
        "ota-channels",
    )
    return templates.TemplateResponse("ota_channels.html", context)


@router.get("/ota/policies")
def ota_policies(request: Request):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    context = build_admin_context(
        request,
        "OTA Policies",
        "ota",
        "ota-policies",
    )
    return templates.TemplateResponse("ota_policies.html", context)


@router.get("/ota/monitoring")
def ota_monitoring(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    failed_logs = (
        db.query(DeviceOTALog)
        .filter(DeviceOTALog.status == "failed")
        .order_by(DeviceOTALog.updated_at.desc())
        .limit(25)
        .all()
    )

    context = build_admin_context(
        request,
        "OTA Monitoring",
        "ota",
        "ota-monitoring",
        failed_logs=failed_logs,
    )
    return templates.TemplateResponse("ota_monitoring.html", context)


@router.get("/erp-allowlist")
def erp_allowlist_page(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    doctypes = (
        db.query(ERPAllowlistEntry)
        .filter(ERPAllowlistEntry.entry_type == ERPAllowlistType.doctype)
        .order_by(ERPAllowlistEntry.value.asc())
        .all()
    )
    methods = (
        db.query(ERPAllowlistEntry)
        .filter(ERPAllowlistEntry.entry_type == ERPAllowlistType.method)
        .order_by(ERPAllowlistEntry.value.asc())
        .all()
    )

    message, error, _ = pop_flash(request)
    context = build_admin_context(
        request,
        "ERP Allowlist",
        "licensing",
        "erp-allowlist",
        doctypes=doctypes,
        methods=methods,
        defaults_doctypes=settings.erp_allowed_doctypes,
        defaults_methods=settings.erp_allowed_methods,
        message=message,
        error=error,
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("erp_allowlist.html", context)


@router.post("/erp-allowlist/seed")
async def seed_allowlist(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/erp-allowlist")
    if csrf_error:
        return csrf_error

    if has_allowlist_entries(db):
        set_flash(request, error="Allowlist already has entries")
        return redirect_to("/admin-ui/erp-allowlist")

    seed_allowlist_from_settings(db)
    if has_allowlist_entries(db):
        set_flash(request, message="Defaults loaded into allowlist")
    else:
        set_flash(request, error="No defaults configured in .env")
    return redirect_to("/admin-ui/erp-allowlist")


@router.post("/erp-allowlist/doctypes")
async def add_allowlist_doctype(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/erp-allowlist")
    if csrf_error:
        return csrf_error

    raw_value = str(form.get("value") or "")
    value = normalize_doctype(raw_value)
    if not value:
        set_flash(request, error="Doctype is required")
        return redirect_to("/admin-ui/erp-allowlist")

    if not has_allowlist_entries(db):
        seed_allowlist_from_settings(db)

    existing = (
        db.query(ERPAllowlistEntry)
        .filter(ERPAllowlistEntry.entry_type == ERPAllowlistType.doctype)
        .all()
    )
    if any(entry.value.lower() == value.lower() for entry in existing):
        set_flash(request, error="Doctype already exists")
        return redirect_to("/admin-ui/erp-allowlist")

    db.add(ERPAllowlistEntry(entry_type=ERPAllowlistType.doctype, value=value))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        set_flash(request, error="Doctype already exists")
        return redirect_to("/admin-ui/erp-allowlist")

    set_flash(request, message="Doctype added")
    return redirect_to("/admin-ui/erp-allowlist")


@router.post("/erp-allowlist/methods")
async def add_allowlist_method(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/erp-allowlist")
    if csrf_error:
        return csrf_error

    raw_value = str(form.get("value") or "")
    value = normalize_method(raw_value)
    if not value:
        set_flash(request, error="Method is required")
        return redirect_to("/admin-ui/erp-allowlist")

    if not has_allowlist_entries(db):
        seed_allowlist_from_settings(db)

    existing = (
        db.query(ERPAllowlistEntry)
        .filter(ERPAllowlistEntry.entry_type == ERPAllowlistType.method)
        .all()
    )
    if any(entry.value.upper() == value for entry in existing):
        set_flash(request, error="Method already exists")
        return redirect_to("/admin-ui/erp-allowlist")

    db.add(ERPAllowlistEntry(entry_type=ERPAllowlistType.method, value=value))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        set_flash(request, error="Method already exists")
        return redirect_to("/admin-ui/erp-allowlist")

    set_flash(request, message="Method added")
    return redirect_to("/admin-ui/erp-allowlist")


@router.post("/erp-allowlist/{entry_id}/delete")
async def delete_allowlist_entry(request: Request, entry_id: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/erp-allowlist")
    if csrf_error:
        return csrf_error

    try:
        entry_uuid = uuid.UUID(entry_id)
    except ValueError:
        set_flash(request, error="Invalid allowlist entry id")
        return redirect_to("/admin-ui/erp-allowlist")

    entry = db.query(ERPAllowlistEntry).filter(ERPAllowlistEntry.id == entry_uuid).first()
    if not entry:
        set_flash(request, error="Allowlist entry not found")
        return redirect_to("/admin-ui/erp-allowlist")

    db.delete(entry)
    db.commit()
    set_flash(request, message="Allowlist entry deleted")
    return redirect_to("/admin-ui/erp-allowlist")
