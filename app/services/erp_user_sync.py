from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import ERPUser
from app.services.erp_user_auth import ERPUserIdentity


def upsert_erp_user_login(
    db: Session,
    tenant_id: UUID,
    identity: ERPUserIdentity,
    now: datetime,
) -> ERPUser:
    username = identity.username.strip()
    user = (
        db.query(ERPUser)
        .filter(
            ERPUser.tenant_id == tenant_id,
            func.lower(ERPUser.erp_username) == username.lower(),
        )
        .first()
    )
    if not user:
        user = ERPUser(
            tenant_id=tenant_id,
            erp_username=username,
        )
        db.add(user)

    user.full_name = identity.full_name
    user.enabled = identity.enabled
    user.erp_roles = list(identity.roles)
    user.last_login_at = now
    return user
