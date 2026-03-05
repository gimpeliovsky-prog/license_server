import base64
import io
import re
from datetime import timedelta
from urllib.parse import quote

import qrcode
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, get_db
from app.config import get_settings
from app.models import LicenseKey, LicenseKeyStatus, Tenant, TenantStatus
from app.services.erp_user_auth import ERPUserAuthError, authenticate_erp_user
from app.services.erp_user_sync import upsert_erp_user_login
from app.services.pairing import create_pairing_token
from app.services.rate_limit import RateLimiter
from app.utils.time import utcnow

router = APIRouter(tags=["public"])
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()
pairing_limiter = RateLimiter(settings.rate_limit_activate_ip_per_minute, 60)

SUBDOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _normalize_subdomain(value: str) -> str:
    text = value.strip().lower()
    if not SUBDOMAIN_RE.fullmatch(text):
        return ""
    return text


def _domain_suffix() -> str:
    suffix = settings.pairing_domain_suffix.strip()
    if suffix.startswith("https://"):
        suffix = suffix[len("https://"):]
    elif suffix.startswith("http://"):
        suffix = suffix[len("http://"):]
    return suffix.strip().lstrip(".").rstrip("/")


def _build_erp_url(subdomain: str) -> str:
    suffix = _domain_suffix()
    return f"https://{subdomain}.{suffix}"


def _build_pair_url(token: str) -> str:
    base = settings.pairing_qr_scheme.strip() or "tsd://pair"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}token={quote(token, safe='')}"


def _render_qr_png_base64(data: str) -> str:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _has_active_license(db: Session, tenant_id) -> bool:
    return (
        db.query(LicenseKey.id)
        .filter(LicenseKey.tenant_id == tenant_id, LicenseKey.status == LicenseKeyStatus.active)
        .first()
        is not None
    )


@router.get("/")
def register_device_page(request: Request):
    return templates.TemplateResponse(
        "register_device.html",
        {
            "request": request,
            "title": "Register New Device",
            "domain_suffix": _domain_suffix(),
            "ttl_minutes": settings.pairing_token_ttl_minutes,
            "error": None,
            "message": None,
            "pair_url": None,
            "qr_image_base64": None,
            "subdomain": "",
            "erp_username": "",
        },
    )


@router.post("/register-device")
async def register_device_submit(request: Request, db: Session = Depends(get_db)):
    client_ip = get_client_ip(request)
    if not pairing_limiter.allow(client_ip):
        return templates.TemplateResponse(
            "register_device.html",
            {
                "request": request,
                "title": "Register New Device",
                "domain_suffix": _domain_suffix(),
                "ttl_minutes": settings.pairing_token_ttl_minutes,
                "error": "Too many attempts. Try again later.",
                "message": None,
                "pair_url": None,
                "qr_image_base64": None,
                "subdomain": "",
                "erp_username": "",
            },
        )

    form = await request.form()
    subdomain = _normalize_subdomain(str(form.get("subdomain") or ""))
    erp_username = str(form.get("erp_username") or "").strip()
    erp_password = str(form.get("erp_password") or "")

    error = None
    message = None
    pair_url = None
    qr_image_base64 = None

    if not subdomain:
        error = "Invalid company code. Use letters, numbers, and hyphen."
    elif not erp_username or not erp_password:
        error = "ERP username and password are required."
    else:
        target_url = _build_erp_url(subdomain)
        tenant = (
            db.query(Tenant)
            .filter(func.lower(Tenant.erpnext_url) == target_url.lower())
            .first()
        )
        now = utcnow()
        if not tenant or tenant.status != TenantStatus.active:
            error = "Tenant not found."
        elif tenant.subscription_expires_at < now:
            error = "Subscription expired."
        elif not _has_active_license(db, tenant.id):
            error = "No active license for this tenant."
        else:
            try:
                identity = authenticate_erp_user(tenant.erpnext_url, erp_username, erp_password)
            except ERPUserAuthError as exc:
                error = str(exc)
            else:
                if not identity.enabled:
                    error = "ERP user disabled."
                else:
                    expires_at = now + timedelta(minutes=max(settings.pairing_token_ttl_minutes, 1))
                    token = create_pairing_token(
                        db=db,
                        tenant_id=tenant.id,
                        identity=identity,
                        expires_at=expires_at,
                        created_ip=get_client_ip(request),
                    )
                    upsert_erp_user_login(db=db, tenant_id=tenant.id, identity=identity, now=now)
                    db.commit()

                    pair_url = _build_pair_url(token)
                    qr_image_base64 = _render_qr_png_base64(pair_url)
                    message = "Scan this QR in the mobile app. The code works once and expires automatically."

    return templates.TemplateResponse(
        "register_device.html",
        {
            "request": request,
            "title": "Register New Device",
            "domain_suffix": _domain_suffix(),
            "ttl_minutes": settings.pairing_token_ttl_minutes,
            "error": error,
            "message": message,
            "pair_url": pair_url,
            "qr_image_base64": qr_image_base64,
            "subdomain": subdomain,
            "erp_username": erp_username,
        },
    )
