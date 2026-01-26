import argparse
import secrets
import uuid
from datetime import date, datetime, time, timedelta, timezone

from app.db import SessionLocal
from app.models import Device, LicenseKey, LicenseKeyStatus, Tenant, TenantStatus
from app.services.erpnext import normalize_erpnext_url
from app.services.license import fingerprint_license_key, hash_license_key
from app.utils.time import utcnow


def parse_datetime(value: str) -> datetime:
    if "T" not in value:
        parsed_date = date.fromisoformat(value)
        parsed = datetime.combine(parsed_date, time(23, 59, 59))
    else:
        parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def print_tenant(tenant: Tenant) -> None:
    print(f"company_code: {tenant.company_code}")
    print(f"status: {tenant.status.value}")
    print(f"subscription_expires_at: {tenant.subscription_expires_at.isoformat()}")
    print(f"erpnext_url: {tenant.erpnext_url}")


def list_tenants(db, status: str | None) -> int:
    query = db.query(Tenant)
    if status:
        query = query.filter(Tenant.status == TenantStatus(status))
    tenants = query.order_by(Tenant.company_code.asc()).all()
    if not tenants:
        print("No tenants found")
        return 0
    for tenant in tenants:
        print(
            f"{tenant.company_code}\t{tenant.status.value}\t{tenant.subscription_expires_at.isoformat()}\t{tenant.erpnext_url}"
        )
    return 0


def show_tenant(db, company_code: str) -> int:
    tenant = db.query(Tenant).filter(Tenant.company_code == company_code).first()
    if not tenant:
        print("Tenant not found")
        return 1
    print_tenant(tenant)
    return 0


def create_tenant(
    db,
    company_code: str,
    erpnext_url: str,
    api_key: str,
    api_secret: str,
    subscription_expires_at: datetime,
    status: str,
) -> int:
    existing = db.query(Tenant).filter(Tenant.company_code == company_code).first()
    if existing:
        print("Tenant already exists")
        return 1
    normalized_url = normalize_erpnext_url(erpnext_url)
    if not normalized_url:
        print("ERPNext URL is required")
        return 1
    tenant = Tenant(
        company_code=company_code,
        erpnext_url=normalized_url,
        api_key=api_key,
        api_secret=api_secret,
        status=TenantStatus(status),
        subscription_expires_at=subscription_expires_at,
    )
    db.add(tenant)
    db.commit()
    print(f"Tenant created: {tenant.id}")
    return 0


def set_subscription(db, company_code: str, expires_at: datetime) -> int:
    tenant = db.query(Tenant).filter(Tenant.company_code == company_code).first()
    if not tenant:
        print("Tenant not found")
        return 1
    tenant.subscription_expires_at = expires_at
    db.commit()
    print("Subscription updated")
    print_tenant(tenant)
    return 0


def add_days(db, company_code: str, days: int) -> int:
    tenant = db.query(Tenant).filter(Tenant.company_code == company_code).first()
    if not tenant:
        print("Tenant not found")
        return 1

    now = utcnow()
    base = tenant.subscription_expires_at
    if base < now:
        base = now
    tenant.subscription_expires_at = base + timedelta(days=days)
    db.commit()
    print("Subscription extended")
    print_tenant(tenant)
    return 0


def set_status(db, company_code: str, status: str) -> int:
    tenant = db.query(Tenant).filter(Tenant.company_code == company_code).first()
    if not tenant:
        print("Tenant not found")
        return 1
    tenant.status = TenantStatus(status)
    db.commit()
    print("Status updated")
    print_tenant(tenant)
    return 0


def list_licenses(db, company_code: str) -> int:
    tenant = db.query(Tenant).filter(Tenant.company_code == company_code).first()
    if not tenant:
        print("Tenant not found")
        return 1
    licenses = (
        db.query(LicenseKey)
        .filter(LicenseKey.tenant_id == tenant.id)
        .order_by(LicenseKey.created_at.desc())
        .all()
    )
    if not licenses:
        print("No license keys found")
        return 0
    for key in licenses:
        print(f"{key.id}\t{key.status.value}\t{key.created_at.isoformat()}")
    return 0


def create_license(db, company_code: str, key: str | None, status: str) -> int:
    tenant = db.query(Tenant).filter(Tenant.company_code == company_code).first()
    if not tenant:
        print("Tenant not found")
        return 1
    license_key = (key or secrets.token_urlsafe(32)).strip()
    if not license_key:
        print("License key invalid")
        return 1
    fingerprint = fingerprint_license_key(license_key) or None
    if fingerprint:
        existing = db.query(LicenseKey).filter(LicenseKey.fingerprint == fingerprint).first()
        if existing:
            print("License key already exists")
            return 1
    license_entry = LicenseKey(
        tenant_id=tenant.id,
        hashed_key=hash_license_key(license_key),
        fingerprint=fingerprint,
        status=LicenseKeyStatus(status),
    )
    db.add(license_entry)
    db.commit()
    print("License key created:")
    print(license_key)
    return 0


def update_license_status(db, license_id: str, status: str) -> int:
    try:
        license_uuid = uuid.UUID(license_id)
    except ValueError:
        print("Invalid license id")
        return 1
    license_key = db.query(LicenseKey).filter(LicenseKey.id == license_uuid).first()
    if not license_key:
        print("License key not found")
        return 1
    license_key.status = LicenseKeyStatus(status)
    db.commit()
    print("License status updated")
    print(f"{license_key.id}\t{license_key.status.value}\t{license_key.created_at.isoformat()}")
    return 0


def list_devices(db, company_code: str) -> int:
    tenant = db.query(Tenant).filter(Tenant.company_code == company_code).first()
    if not tenant:
        print("Tenant not found")
        return 1
    devices = (
        db.query(Device)
        .filter(Device.tenant_id == tenant.id)
        .order_by(Device.created_at.desc())
        .all()
    )
    if not devices:
        print("No devices found")
        return 0
    for device in devices:
        last_seen = device.last_seen.isoformat() if device.last_seen else "-"
        print(f"{device.device_id}\t{device.revoked}\t{last_seen}")
    return 0


def set_device_revoked(db, company_code: str, device_id: str, revoked: bool) -> int:
    tenant = db.query(Tenant).filter(Tenant.company_code == company_code).first()
    if not tenant:
        print("Tenant not found")
        return 1
    device = (
        db.query(Device)
        .filter(Device.tenant_id == tenant.id, Device.device_id == device_id)
        .first()
    )
    if not device:
        print("Device not found")
        return 1
    device.revoked = revoked
    db.commit()
    status = "revoked" if revoked else "active"
    print(f"Device {status}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Admin console for license management")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_tenants_parser = subparsers.add_parser("list-tenants")
    list_tenants_parser.add_argument("--status", choices=[s.value for s in TenantStatus], default=None)

    show_tenant_parser = subparsers.add_parser("show-tenant")
    show_tenant_parser.add_argument("--company-code", required=True)

    create_tenant_parser = subparsers.add_parser("create-tenant")
    create_tenant_parser.add_argument("--company-code", required=True)
    create_tenant_parser.add_argument("--erpnext-url", required=True)
    create_tenant_parser.add_argument("--api-key", required=True)
    create_tenant_parser.add_argument("--api-secret", required=True)
    create_tenant_parser.add_argument("--subscription-expires-at", required=True)
    create_tenant_parser.add_argument(
        "--status", default=TenantStatus.active.value, choices=[s.value for s in TenantStatus]
    )

    set_subscription_parser = subparsers.add_parser("set-subscription")
    set_subscription_parser.add_argument("--company-code", required=True)
    set_subscription_parser.add_argument("--expires-at", required=True)

    add_days_parser = subparsers.add_parser("add-days")
    add_days_parser.add_argument("--company-code", required=True)
    add_days_parser.add_argument("--days", type=int, required=True)

    set_status_parser = subparsers.add_parser("set-status")
    set_status_parser.add_argument("--company-code", required=True)
    set_status_parser.add_argument("--status", choices=[s.value for s in TenantStatus], required=True)

    list_licenses_parser = subparsers.add_parser("list-licenses")
    list_licenses_parser.add_argument("--company-code", required=True)

    create_license_parser = subparsers.add_parser("create-license")
    create_license_parser.add_argument("--company-code", required=True)
    create_license_parser.add_argument("--key", default=None)
    create_license_parser.add_argument(
        "--status", default=LicenseKeyStatus.active.value, choices=[s.value for s in LicenseKeyStatus]
    )

    revoke_license_parser = subparsers.add_parser("revoke-license")
    revoke_license_parser.add_argument("--license-id", required=True)

    activate_license_parser = subparsers.add_parser("activate-license")
    activate_license_parser.add_argument("--license-id", required=True)

    list_devices_parser = subparsers.add_parser("list-devices")
    list_devices_parser.add_argument("--company-code", required=True)

    revoke_device_parser = subparsers.add_parser("revoke-device")
    revoke_device_parser.add_argument("--company-code", required=True)
    revoke_device_parser.add_argument("--device-id", required=True)

    unrevoke_device_parser = subparsers.add_parser("unrevoke-device")
    unrevoke_device_parser.add_argument("--company-code", required=True)
    unrevoke_device_parser.add_argument("--device-id", required=True)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.command == "list-tenants":
            return list_tenants(db, args.status)
        if args.command == "show-tenant":
            return show_tenant(db, args.company_code)
        if args.command == "create-tenant":
            return create_tenant(
                db,
                args.company_code,
                args.erpnext_url,
                args.api_key,
                args.api_secret,
                parse_datetime(args.subscription_expires_at),
                args.status,
            )
        if args.command == "set-subscription":
            return set_subscription(db, args.company_code, parse_datetime(args.expires_at))
        if args.command == "add-days":
            return add_days(db, args.company_code, args.days)
        if args.command == "set-status":
            return set_status(db, args.company_code, args.status)
        if args.command == "list-licenses":
            return list_licenses(db, args.company_code)
        if args.command == "create-license":
            return create_license(db, args.company_code, args.key, args.status)
        if args.command == "revoke-license":
            return update_license_status(db, args.license_id, LicenseKeyStatus.revoked.value)
        if args.command == "activate-license":
            return update_license_status(db, args.license_id, LicenseKeyStatus.active.value)
        if args.command == "list-devices":
            return list_devices(db, args.company_code)
        if args.command == "revoke-device":
            return set_device_revoked(db, args.company_code, args.device_id, True)
        if args.command == "unrevoke-device":
            return set_device_revoked(db, args.company_code, args.device_id, False)
        print("Unknown command")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
