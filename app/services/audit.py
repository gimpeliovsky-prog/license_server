from typing import Any

from sqlalchemy.orm import Session

from app.api.deps import RequestContext
from app.models import AuditLog


def write_audit_log(
    db: Session,
    context: RequestContext,
    *,
    action: str,
    meta: dict[str, Any] | None = None,
) -> None:
    payload = dict(meta or {})
    payload.setdefault("erp_username", context.erp_username)
    payload.setdefault("app_permissions", sorted(context.app_permissions))
    db.add(
        AuditLog(
            tenant_id=context.tenant.id,
            device_id=context.device.id if context.device else None,
            action=action,
            meta=payload,
        )
    )
