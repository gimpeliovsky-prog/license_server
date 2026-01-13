from dataclasses import dataclass
import hmac
from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import Device, Tenant, TenantStatus
from app.services.auth import TokenData, TokenExpired, TokenInvalid, decode_access_token
from app.services.rate_limit import RateLimiter
from app.services.subscription import evaluate_subscription
from app.utils.time import utcnow

settings = get_settings()

bearer_scheme = HTTPBearer(auto_error=False)
activate_limiter = RateLimiter(settings.rate_limit_activate_per_minute, 60)
activate_ip_limiter = RateLimiter(settings.rate_limit_activate_ip_per_minute, 60)
refresh_limiter = RateLimiter(settings.rate_limit_refresh_per_minute, 60)


@dataclass
class RequestContext:
    tenant: Tenant
    device: Device | None
    token: TokenData
    subscription_active: bool
    grace_active: bool


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def enforce_rate_limit(limiter: RateLimiter, key: str) -> None:
    if not limiter.allow(key):
        raise HTTPException(status_code=429, detail="Too many requests")


def rate_limit_activate(request: Request, company_code: str) -> None:
    client_ip = get_client_ip(request)
    enforce_rate_limit(activate_ip_limiter, client_ip)
    enforce_rate_limit(activate_limiter, f"{client_ip}:{company_code}")


def rate_limit_refresh(request: Request) -> None:
    client_ip = get_client_ip(request)
    enforce_rate_limit(refresh_limiter, client_ip)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin token not configured")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Admin token invalid")


def get_token_data(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> TokenData:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        return decode_access_token(credentials.credentials)
    except TokenExpired:
        raise HTTPException(status_code=401, detail="Token expired")
    except TokenInvalid:
        raise HTTPException(status_code=401, detail="Token invalid")


def get_request_context(
    token_data: TokenData = Depends(get_token_data),
    db: Session = Depends(get_db),
) -> RequestContext:
    tenant = db.query(Tenant).filter(Tenant.id == token_data.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=401, detail="Tenant not found")

    if tenant.status != TenantStatus.active:
        raise HTTPException(status_code=403, detail="Tenant disabled")

    now = utcnow()
    state = evaluate_subscription(
        subscription_expires_at=tenant.subscription_expires_at,
        issued_at=token_data.issued_at,
        now=now,
    )
    if not state.allowed:
        raise HTTPException(status_code=403, detail="Subscription expired")

    device = None
    if token_data.device_id:
        device = (
            db.query(Device)
            .filter(Device.tenant_id == tenant.id, Device.device_id == token_data.device_id)
            .first()
        )
        if device and device.revoked:
            raise HTTPException(status_code=403, detail="Device revoked")
        if not device:
            device = Device(device_id=token_data.device_id, tenant_id=tenant.id, last_seen=now)
            db.add(device)
        else:
            device.last_seen = now
        db.commit()

    return RequestContext(
        tenant=tenant,
        device=device,
        token=token_data,
        subscription_active=state.subscription_active,
        grace_active=state.grace_active,
    )
