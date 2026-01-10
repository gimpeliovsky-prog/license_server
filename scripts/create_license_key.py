import argparse
import secrets
import sys

from app.db import SessionLocal
from app.models import LicenseKey, LicenseKeyStatus, Tenant
from app.services.license import fingerprint_license_key, hash_license_key


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a new license key")
    parser.add_argument("--company-code", required=True)
    parser.add_argument("--key", default=None)
    parser.add_argument("--status", default=LicenseKeyStatus.active.value, choices=[s.value for s in LicenseKeyStatus])
    args = parser.parse_args()

    license_key = (args.key or secrets.token_urlsafe(32)).strip()
    if not license_key:
        print("License key invalid")
        return 1
    fingerprint = fingerprint_license_key(license_key) or None

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.company_code == args.company_code).first()
        if not tenant:
            print("Tenant not found")
            return 1

        license_entry = LicenseKey(
            tenant_id=tenant.id,
            hashed_key=hash_license_key(license_key),
            fingerprint=fingerprint,
            status=LicenseKeyStatus(args.status),
        )
        db.add(license_entry)
        db.commit()
        print("License key created:")
        print(license_key)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
