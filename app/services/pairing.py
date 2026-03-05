import hashlib
import secrets
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import DevicePairingToken
from app.services.erp_user_auth import ERPUserIdentity


def hash_pairing_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_pairing_token(
    db: Session,
    tenant_id,
    identity: ERPUserIdentity,
    expires_at: datetime,
    created_ip: str | None,
) -> str:
    token = secrets.token_urlsafe(32)
    entry = DevicePairingToken(
        tenant_id=tenant_id,
        token_hash=hash_pairing_token(token),
        erp_username=identity.username,
        erp_roles=list(identity.roles),
        full_name=identity.full_name,
        enabled=identity.enabled,
        expires_at=expires_at,
        created_ip=(created_ip or "").strip() or None,
    )
    db.add(entry)
    return token
