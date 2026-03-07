from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    Device,
    DevicePairingToken,
    ERPIdempotencyEntry,
    ERPUser,
    LicenseKey,
    OTAAccess,
    Tenant,
)


def delete_tenant_with_dependencies(db: Session, tenant: Tenant) -> None:
    device_ids = [row.id for row in db.query(Device.id).filter(Device.tenant_id == tenant.id).all()]
    license_ids = [row.id for row in db.query(LicenseKey.id).filter(LicenseKey.tenant_id == tenant.id).all()]

    ota_access_filters = [OTAAccess.tenant_id == tenant.id]
    if license_ids:
        ota_access_filters.append(OTAAccess.license_key_id.in_(license_ids))
    db.query(OTAAccess).filter(or_(*ota_access_filters)).delete(synchronize_session=False)

    db.query(ERPUser).filter(ERPUser.tenant_id == tenant.id).delete(synchronize_session=False)
    db.query(DevicePairingToken).filter(DevicePairingToken.tenant_id == tenant.id).delete(
        synchronize_session=False
    )
    db.query(ERPIdempotencyEntry).filter(ERPIdempotencyEntry.tenant_id == tenant.id).delete(
        synchronize_session=False
    )

    audit_query = db.query(AuditLog)
    if device_ids:
        audit_query = audit_query.filter(
            (AuditLog.tenant_id == tenant.id) | (AuditLog.device_id.in_(device_ids))
        )
    else:
        audit_query = audit_query.filter(AuditLog.tenant_id == tenant.id)
    audit_query.delete(synchronize_session=False)

    db.delete(tenant)
