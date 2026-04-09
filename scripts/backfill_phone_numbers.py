import argparse
from collections.abc import Iterable

from app.db import SessionLocal
from app.models import AIConversation, BuyerChannelIdentity, ERPContactSnapshot, IdentityReviewCase, Tenant, TenantChannel
from app.services.phone_numbers import normalize_phone


def _tenant_scope(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    return text or None


def _changed(before: str | None, after: str | None) -> bool:
    return (before or None) != (after or None)


def _backfill_buyer_identities(db, tenant_ids: set) -> int:
    query = db.query(BuyerChannelIdentity)
    if tenant_ids:
        query = query.filter(BuyerChannelIdentity.tenant_id.in_(tenant_ids))
    count = 0
    for row in query.yield_per(500):
        normalized = normalize_phone(row.phone)
        if _changed(row.phone, normalized):
            row.phone = normalized
            count += 1
    return count


def _backfill_ai_conversations(db, tenant_ids: set) -> int:
    query = db.query(AIConversation)
    if tenant_ids:
        query = query.filter(AIConversation.tenant_id.in_(tenant_ids))
    count = 0
    for row in query.yield_per(500):
        normalized = normalize_phone(row.buyer_phone)
        if _changed(row.buyer_phone, normalized):
            row.buyer_phone = normalized
            count += 1
    return count


def _backfill_identity_review_cases(db, tenant_ids: set) -> int:
    query = db.query(IdentityReviewCase)
    if tenant_ids:
        query = query.filter(IdentityReviewCase.tenant_id.in_(tenant_ids))
    count = 0
    for row in query.yield_per(500):
        normalized = normalize_phone(row.phone)
        if _changed(row.phone, normalized):
            row.phone = normalized
            count += 1
    return count


def _backfill_contact_snapshots(db, tenant_ids: set) -> int:
    query = db.query(ERPContactSnapshot)
    if tenant_ids:
        query = query.filter(ERPContactSnapshot.tenant_id.in_(tenant_ids))
    count = 0
    for row in query.yield_per(500):
        next_phone = normalize_phone(row.phone)
        next_mobile = normalize_phone(row.mobile_no)
        if _changed(row.normalized_phone, next_phone):
            row.normalized_phone = next_phone
            count += 1
        if _changed(row.normalized_mobile_no, next_mobile):
            row.normalized_mobile_no = next_mobile
            count += 1
    return count


def _backfill_tenant_channels(db, tenant_ids: set) -> int:
    query = db.query(TenantChannel)
    if tenant_ids:
        query = query.filter(TenantChannel.tenant_id.in_(tenant_ids))
    count = 0
    for row in query.yield_per(200):
        next_tg = normalize_phone(row.tg_phone_number)
        next_wa = normalize_phone(row.wa_number)
        if _changed(row.tg_phone_number, next_tg):
            row.tg_phone_number = next_tg
            count += 1
        if _changed(row.wa_number, next_wa):
            row.wa_number = next_wa
            count += 1
    return count


def _resolve_tenant_ids(db, company_codes: Iterable[str]) -> set:
    normalized = [_tenant_scope(value) for value in company_codes]
    filtered = [value for value in normalized if value]
    if not filtered:
        return set()
    rows = db.query(Tenant.id).filter(Tenant.company_code.in_(filtered)).all()
    return {row[0] for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill stored phone numbers into canonical Israeli-aware E.164 format")
    parser.add_argument(
        "--company-code",
        action="append",
        default=[],
        help="Optional tenant company code filter. Can be provided multiple times.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the changes. Without this flag the script runs in dry-run mode and rolls back.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        tenant_ids = _resolve_tenant_ids(db, args.company_code)
        counters = {
            "buyer_channel_identities": _backfill_buyer_identities(db, tenant_ids),
            "ai_conversations": _backfill_ai_conversations(db, tenant_ids),
            "identity_review_cases": _backfill_identity_review_cases(db, tenant_ids),
            "erp_contact_snapshots": _backfill_contact_snapshots(db, tenant_ids),
            "tenant_channels": _backfill_tenant_channels(db, tenant_ids),
        }
        total_changes = sum(counters.values())

        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"[{mode}] Phone normalization backfill summary")
        if args.company_code:
            print(f"Tenants: {', '.join(args.company_code)}")
        for bucket, changed in counters.items():
            print(f"  {bucket}: {changed}")
        print(f"  total changes: {total_changes}")

        if args.apply:
            db.commit()
            print("Changes committed.")
        else:
            db.rollback()
            print("Dry-run complete. No changes committed.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
