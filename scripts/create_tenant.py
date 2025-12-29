import argparse
import sys
from datetime import datetime, timezone

from app.db import SessionLocal
from app.models import Tenant, TenantStatus


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a new tenant")
    parser.add_argument("--company-code", required=True)
    parser.add_argument("--erpnext-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--api-secret", required=True)
    parser.add_argument("--subscription-expires-at", required=True)
    parser.add_argument("--status", default=TenantStatus.active.value, choices=[s.value for s in TenantStatus])
    args = parser.parse_args()

    db = SessionLocal()
    try:
        existing = db.query(Tenant).filter(Tenant.company_code == args.company_code).first()
        if existing:
            print("Tenant already exists")
            return 1

        tenant = Tenant(
            company_code=args.company_code,
            erpnext_url=args.erpnext_url.rstrip("/"),
            api_key=args.api_key,
            api_secret=args.api_secret,
            status=TenantStatus(args.status),
            subscription_expires_at=parse_datetime(args.subscription_expires_at),
        )
        db.add(tenant)
        db.commit()
        print(f"Tenant created: {tenant.id}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
