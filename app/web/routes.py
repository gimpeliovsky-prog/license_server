import secrets
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import get_settings
from app.models import (
    Device,
    ERPAllowlistEntry,
    ERPAllowlistType,
    LicenseKey,
    LicenseKeyStatus,
    Tenant,
    TenantStatus,
)
from app.services.allowlist import (
    has_allowlist_entries,
    normalize_doctype,
    normalize_method,
    seed_allowlist_from_settings,
)
from app.services.erpnext import normalize_erpnext_url
from app.services.license import fingerprint_license_key, hash_license_key
from app.utils.time import utcnow

router = APIRouter(prefix="/admin-ui", tags=["admin-ui"])
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()


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


def require_admin_or_redirect(request: Request):
    if not is_admin(request):
        return RedirectResponse("/admin-ui/login", status_code=303)
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


@router.get("/")
def index() -> RedirectResponse:
    return redirect_to("/admin-ui/tenants")


@router.get("/login")
def login_page(request: Request):
    if is_admin(request):
        return redirect_to("/admin-ui/tenants")
    message, error, _ = pop_flash(request)
    context = {
        "request": request,
        "title": "Admin Login",
        "message": message,
        "error": error,
        "admin_token_configured": bool(settings.admin_token),
        "is_admin": False,
    }
    return templates.TemplateResponse("login.html", context)


@router.post("/login")
async def login(request: Request):
    if not settings.admin_token:
        set_flash(request, error="ADMIN_TOKEN is not configured")
        return redirect_to("/admin-ui/login")

    form = await request.form()
    token = str(form.get("admin_token") or "")
    if token != settings.admin_token:
        set_flash(request, error="Invalid admin token")
        return redirect_to("/admin-ui/login")

    request.session["is_admin"] = True
    set_flash(request, message="Logged in")
    return redirect_to("/admin-ui/tenants")


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

    context = {
        "request": request,
        "title": "Tenants",
        "tenants": tenants,
        "message": message,
        "error": error,
        "statuses": [status.value for status in TenantStatus],
        "is_admin": True,
    }
    return templates.TemplateResponse("tenants.html", context)


@router.post("/tenants")
async def create_tenant(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    company_code = str(form.get("company_code") or "").strip()
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
    devices = (
        db.query(Device)
        .filter(Device.tenant_id == tenant.id)
        .order_by(Device.created_at.desc())
        .all()
    )
    message, error, license_key = pop_flash(request)

    context = {
        "request": request,
        "title": f"Tenant {tenant.company_code}",
        "tenant": tenant,
        "licenses": licenses,
        "devices": devices,
        "message": message,
        "error": error,
        "new_license_key": license_key,
        "tenant_statuses": [status.value for status in TenantStatus],
        "license_statuses": [status.value for status in LicenseKeyStatus],
        "is_admin": True,
    }
    return templates.TemplateResponse("tenant_detail.html", context)


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

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

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
    license_key_raw = str(form.get("license_key") or "").strip()
    status_raw = str(form.get("status") or LicenseKeyStatus.active.value).strip()

    status = parse_license_status(status_raw)
    if not status:
        set_flash(request, error="Invalid license status")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    license_key = license_key_raw or secrets.token_urlsafe(32)
    fingerprint = fingerprint_license_key(license_key) or None
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


@router.post("/tenants/{company_code}/devices/{device_id}")
async def update_device(request: Request, company_code: str, device_id: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

    form = await request.form()
    revoked_raw = str(form.get("revoked") or "").strip().lower()
    revoked = revoked_raw == "true"

    device = (
        db.query(Device)
        .filter(Device.tenant_id == tenant.id, Device.device_id == device_id)
        .first()
    )
    if not device:
        set_flash(request, error="Device not found")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    device.revoked = revoked
    db.commit()
    set_flash(request, message="Device updated")
    return redirect_to(f"/admin-ui/tenants/{company_code}")


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
    context = {
        "request": request,
        "title": "ERP Allowlist",
        "doctypes": doctypes,
        "methods": methods,
        "defaults_doctypes": settings.erp_allowed_doctypes,
        "defaults_methods": settings.erp_allowed_methods,
        "message": message,
        "error": error,
        "is_admin": True,
    }
    return templates.TemplateResponse("erp_allowlist.html", context)


@router.post("/erp-allowlist/seed")
async def seed_allowlist(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

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
