from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.models import BuyerChannelIdentity, ERPContactCustomerLink, ERPContactSnapshot, ERPCRMSyncState, ERPCustomerSnapshot, Tenant, TenantStatus
from app.services.phone_numbers import normalize_phone
from app.services.erpnext import ERPNextError, request_tenant_erpnext
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

CUSTOMER_STREAM = "customers"
CONTACT_STREAM = "contacts"

def parse_erp_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _cursor_value(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat(sep=" ")


def _is_after_cursor(modified_at: datetime | None, docname: str, cursor_modified_at: datetime | None, cursor_name: str | None) -> bool:
    if not modified_at or not cursor_modified_at:
        return True
    if modified_at > cursor_modified_at:
        return True
    if modified_at < cursor_modified_at:
        return False
    return docname > str(cursor_name or "")


def _get_sync_state(db: Session, tenant_id, stream_name: str) -> ERPCRMSyncState:
    state = (
        db.query(ERPCRMSyncState)
        .filter(ERPCRMSyncState.tenant_id == tenant_id, ERPCRMSyncState.stream_name == stream_name)
        .first()
    )
    if not state:
        state = ERPCRMSyncState(tenant_id=tenant_id, stream_name=stream_name)
        db.add(state)
        db.flush()
    return state


def _fetch_list(
    tenant: Tenant,
    doctype: str,
    *,
    fields: list[str],
    cursor_modified_at: datetime | None,
    page_size: int,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "fields": json.dumps(fields, separators=(",", ":")),
        "order_by": "modified asc, name asc",
        "limit_page_length": page_size,
    }
    if cursor_modified_at:
        params["filters"] = json.dumps([["modified", ">=", _cursor_value(cursor_modified_at)]], separators=(",", ":"))
    response = request_tenant_erpnext(tenant, "GET", f"/api/resource/{doctype}", params=params)
    if response.status_code != 200:
        raise ERPNextError(f"ERPNext {doctype} sync failed with status {response.status_code}")
    payload = response.json().get("data", [])
    return payload if isinstance(payload, list) else []


def _fetch_contact_detail(tenant: Tenant, contact_id: str) -> dict[str, Any]:
    response = request_tenant_erpnext(tenant, "GET", f"/api/resource/Contact/{quote(contact_id, safe='')}")
    if response.status_code != 200:
        raise ERPNextError(f"ERPNext Contact detail sync failed for {contact_id} with status {response.status_code}")
    payload = response.json().get("data", {})
    return payload if isinstance(payload, dict) else {}


def _upsert_customer_snapshot(db: Session, tenant_id, row: dict[str, Any]) -> None:
    erp_customer_id = str(row.get("name") or "").strip()
    if not erp_customer_id:
        return
    snapshot = (
        db.query(ERPCustomerSnapshot)
        .filter(ERPCustomerSnapshot.tenant_id == tenant_id, ERPCustomerSnapshot.erp_customer_id == erp_customer_id)
        .first()
    )
    if not snapshot:
        snapshot = ERPCustomerSnapshot(tenant_id=tenant_id, erp_customer_id=erp_customer_id)
        db.add(snapshot)
    snapshot.customer_name = str(row.get("customer_name") or "").strip() or None
    snapshot.customer_type = str(row.get("customer_type") or "").strip() or None
    snapshot.customer_group = str(row.get("customer_group") or "").strip() or None
    snapshot.territory = str(row.get("territory") or "").strip() or None
    snapshot.modified_at_erp = parse_erp_datetime(row.get("modified"))
    snapshot.synced_at = utcnow()


def _replace_contact_links(db: Session, tenant_id, contact_id: str, modified_at_erp: datetime | None, detail: dict[str, Any]) -> None:
    db.query(ERPContactCustomerLink).filter(
        ERPContactCustomerLink.tenant_id == tenant_id,
        ERPContactCustomerLink.erp_contact_id == contact_id,
    ).delete(synchronize_session=False)
    links = detail.get("links")
    if not isinstance(links, list):
        return
    now = utcnow()
    for row in links:
        if not isinstance(row, dict):
            continue
        if str(row.get("link_doctype") or "").strip() != "Customer":
            continue
        customer_id = str(row.get("link_name") or "").strip()
        if not customer_id:
            continue
        db.add(
            ERPContactCustomerLink(
                tenant_id=tenant_id,
                erp_contact_id=contact_id,
                erp_customer_id=customer_id,
                modified_at_erp=modified_at_erp,
                synced_at=now,
            )
        )


def _upsert_contact_snapshot(db: Session, tenant: Tenant, row: dict[str, Any]) -> None:
    erp_contact_id = str(row.get("name") or "").strip()
    if not erp_contact_id:
        return
    detail = _fetch_contact_detail(tenant, erp_contact_id)
    modified_at_erp = parse_erp_datetime(detail.get("modified") or row.get("modified"))
    snapshot = (
        db.query(ERPContactSnapshot)
        .filter(ERPContactSnapshot.tenant_id == tenant.id, ERPContactSnapshot.erp_contact_id == erp_contact_id)
        .first()
    )
    if not snapshot:
        snapshot = ERPContactSnapshot(tenant_id=tenant.id, erp_contact_id=erp_contact_id)
        db.add(snapshot)
    snapshot.full_name = str(detail.get("full_name") or row.get("full_name") or "").strip() or None
    snapshot.first_name = str(detail.get("first_name") or row.get("first_name") or "").strip() or None
    snapshot.last_name = str(detail.get("last_name") or row.get("last_name") or "").strip() or None
    snapshot.email_id = str(detail.get("email_id") or row.get("email_id") or "").strip() or None
    snapshot.phone = str(detail.get("phone") or row.get("phone") or "").strip() or None
    snapshot.mobile_no = str(detail.get("mobile_no") or row.get("mobile_no") or "").strip() or None
    snapshot.normalized_phone = normalize_phone(snapshot.phone)
    snapshot.normalized_mobile_no = normalize_phone(snapshot.mobile_no)
    snapshot.company_name = str(detail.get("company_name") or row.get("company_name") or "").strip() or None
    snapshot.modified_at_erp = modified_at_erp
    snapshot.synced_at = utcnow()
    _replace_contact_links(db, tenant.id, erp_contact_id, modified_at_erp, detail)


def _sync_stream(db: Session, tenant: Tenant, stream_name: str, *, page_size: int) -> int:
    state = _get_sync_state(db, tenant.id, stream_name)
    state.last_started_at = utcnow()
    state.last_error_at = None
    state.last_error_text = None
    db.flush()

    delta_count = 0
    while True:
        if stream_name == CUSTOMER_STREAM:
            rows = _fetch_list(
                tenant,
                "Customer",
                fields=["name", "customer_name", "customer_type", "customer_group", "territory", "modified"],
                cursor_modified_at=state.cursor_modified_at,
                page_size=page_size,
            )
        else:
            rows = _fetch_list(
                tenant,
                "Contact",
                fields=["name", "full_name", "first_name", "last_name", "email_id", "phone", "mobile_no", "company_name", "modified"],
                cursor_modified_at=state.cursor_modified_at,
                page_size=page_size,
            )

        filtered: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            docname = str(row.get("name") or "").strip()
            modified_at = parse_erp_datetime(row.get("modified"))
            if docname and _is_after_cursor(modified_at, docname, state.cursor_modified_at, state.cursor_name):
                filtered.append(row)

        if not filtered:
            break

        for row in filtered:
            if stream_name == CUSTOMER_STREAM:
                _upsert_customer_snapshot(db, tenant.id, row)
            else:
                _upsert_contact_snapshot(db, tenant, row)
            state.cursor_modified_at = parse_erp_datetime(row.get("modified"))
            state.cursor_name = str(row.get("name") or "").strip() or None
            delta_count += 1

        state.last_delta_count = delta_count
        db.commit()

        if len(filtered) < page_size:
            break

    state.last_completed_at = utcnow()
    state.last_delta_count = delta_count
    db.commit()
    return delta_count


def resolve_buyer_from_snapshots(db: Session, tenant: Tenant, normalized_phone: str | None) -> dict[str, str | None] | None:
    if not normalized_phone:
        return None
    contact = (
        db.query(ERPContactSnapshot)
        .filter(
            ERPContactSnapshot.tenant_id == tenant.id,
            (ERPContactSnapshot.normalized_mobile_no == normalized_phone)
            | (ERPContactSnapshot.normalized_phone == normalized_phone),
        )
        .order_by(ERPContactSnapshot.modified_at_erp.desc(), ERPContactSnapshot.updated_at.desc())
        .first()
    )
    if not contact:
        return None
    link = (
        db.query(ERPContactCustomerLink)
        .filter(
            ERPContactCustomerLink.tenant_id == tenant.id,
            ERPContactCustomerLink.erp_contact_id == contact.erp_contact_id,
        )
        .order_by(ERPContactCustomerLink.modified_at_erp.desc(), ERPContactCustomerLink.updated_at.desc())
        .first()
    )
    if not link:
        return {
            "erp_contact_id": contact.erp_contact_id,
            "contact_name": contact.full_name,
            "erp_customer_id": None,
            "erp_customer_name": None,
        }
    customer = (
        db.query(ERPCustomerSnapshot)
        .filter(
            ERPCustomerSnapshot.tenant_id == tenant.id,
            ERPCustomerSnapshot.erp_customer_id == link.erp_customer_id,
        )
        .first()
    )
    return {
        "erp_contact_id": contact.erp_contact_id,
        "contact_name": contact.full_name,
        "erp_customer_id": link.erp_customer_id,
        "erp_customer_name": customer.customer_name if customer else None,
    }


def reconcile_buyer_identities(db: Session, tenant: Tenant) -> int:
    identities = db.query(BuyerChannelIdentity).filter(BuyerChannelIdentity.tenant_id == tenant.id).all()
    updated = 0
    for identity in identities:
        resolved: dict[str, str | None] | None = None
        if identity.erp_contact_id:
            contact = (
                db.query(ERPContactSnapshot)
                .filter(
                    ERPContactSnapshot.tenant_id == tenant.id,
                    ERPContactSnapshot.erp_contact_id == identity.erp_contact_id,
                )
                .first()
            )
            if contact:
                link = (
                    db.query(ERPContactCustomerLink)
                    .filter(
                        ERPContactCustomerLink.tenant_id == tenant.id,
                        ERPContactCustomerLink.erp_contact_id == contact.erp_contact_id,
                    )
                    .order_by(ERPContactCustomerLink.modified_at_erp.desc(), ERPContactCustomerLink.updated_at.desc())
                    .first()
                )
                customer_name = None
                customer_id = link.erp_customer_id if link else identity.erp_customer_id
                if customer_id:
                    customer = (
                        db.query(ERPCustomerSnapshot)
                        .filter(
                            ERPCustomerSnapshot.tenant_id == tenant.id,
                            ERPCustomerSnapshot.erp_customer_id == customer_id,
                        )
                        .first()
                    )
                    customer_name = customer.customer_name if customer else identity.erp_customer_name
                resolved = {
                    "erp_contact_id": contact.erp_contact_id,
                    "contact_name": contact.full_name,
                    "erp_customer_id": customer_id,
                    "erp_customer_name": customer_name,
                    "phone": contact.normalized_mobile_no or contact.normalized_phone,
                }
        if not resolved and identity.phone:
            resolved = resolve_buyer_from_snapshots(db, tenant, identity.phone)
            if resolved:
                resolved["phone"] = identity.phone

        if not resolved:
            continue
        changed = False
        next_contact_id = resolved.get("erp_contact_id")
        next_customer_id = resolved.get("erp_customer_id")
        next_customer_name = resolved.get("erp_customer_name")
        next_contact_name = resolved.get("contact_name")
        next_phone = normalize_phone(resolved.get("phone") or identity.phone)
        if identity.erp_contact_id != next_contact_id:
            identity.erp_contact_id = next_contact_id
            changed = True
        if identity.erp_customer_id != next_customer_id:
            identity.erp_customer_id = next_customer_id
            changed = True
        if identity.erp_customer_name != next_customer_name:
            identity.erp_customer_name = next_customer_name
            changed = True
        if identity.full_name != next_contact_name:
            identity.full_name = next_contact_name
            changed = True
        if next_phone and identity.phone != next_phone:
            identity.phone = next_phone
            changed = True
        if changed:
            updated += 1
    if updated:
        db.commit()
    return updated


def sync_tenant_crm_identity(db: Session, tenant: Tenant, *, page_size: int = 200) -> dict[str, int]:
    if tenant.status != TenantStatus.active:
        return {"customers": 0, "contacts": 0, "identities": 0}
    customer_count = 0
    contact_count = 0
    try:
        customer_count = _sync_stream(db, tenant, CUSTOMER_STREAM, page_size=page_size)
        contact_count = _sync_stream(db, tenant, CONTACT_STREAM, page_size=page_size)
        identity_count = reconcile_buyer_identities(db, tenant)
        return {"customers": customer_count, "contacts": contact_count, "identities": identity_count}
    except Exception as exc:
        logger.exception("CRM identity sync failed for tenant %s", tenant.company_code)
        now = utcnow()
        for stream_name in (CUSTOMER_STREAM, CONTACT_STREAM):
            state = _get_sync_state(db, tenant.id, stream_name)
            state.last_error_at = now
            state.last_error_text = str(exc)[:1000]
        db.commit()
        raise


def sync_all_active_tenants(db: Session, *, page_size: int = 200) -> dict[str, dict[str, int]]:
    results: dict[str, dict[str, int]] = {}
    tenants = db.query(Tenant).filter(Tenant.status == TenantStatus.active).order_by(Tenant.company_code.asc()).all()
    for tenant in tenants:
        try:
            results[tenant.company_code] = sync_tenant_crm_identity(db, tenant, page_size=page_size)
        except Exception:
            logger.exception("CRM identity sync failed for tenant %s during batch sync", tenant.company_code)
            db.rollback()
            results[tenant.company_code] = {"customers": 0, "contacts": 0, "identities": 0}
    return results
