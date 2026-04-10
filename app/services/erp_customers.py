from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.services.crm_identity_sync import resolve_buyer_from_snapshots
from app.services.erpnext import request_tenant_erpnext
from app.services.phone_numbers import normalize_phone, phone_match_variants


def list_customers(
    tenant,
    *,
    fields: str,
    limit_start: int | None = None,
    limit_page_length: int | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"fields": fields}
    if limit_start is not None:
        params["limit_start"] = limit_start
    if limit_page_length is not None:
        params["limit_page_length"] = limit_page_length
    response = request_tenant_erpnext(
        tenant,
        "GET",
        "/api/resource/Customer",
        params=params,
    )
    if response.status_code != 200:
        return []
    payload = response.json().get("data", [])
    return payload if isinstance(payload, list) else []


def get_customer_detail(
    tenant,
    customer_id: str,
    *,
    fields: str | None = None,
) -> dict[str, Any] | None:
    safe_name = quote(str(customer_id or "").strip(), safe="")
    if not safe_name:
        return None
    response = request_tenant_erpnext(
        tenant,
        "GET",
        f"/api/resource/Customer/{safe_name}",
        params={"fields": fields} if fields else None,
    )
    if response.status_code != 200:
        return None
    payload = response.json().get("data", {})
    return payload if isinstance(payload, dict) else None


def load_sales_history(tenant, erp_customer_id: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not erp_customer_id:
        return [], []

    sales_orders: list[dict[str, Any]] = []
    sales_invoices: list[dict[str, Any]] = []

    sales_order_resp = request_tenant_erpnext(
        tenant,
        "GET",
        "/api/resource/Sales Order",
        params={
            "fields": json.dumps(["name", "transaction_date", "status", "grand_total", "currency"]),
            "filters": json.dumps([["customer", "=", erp_customer_id]]),
            "order_by": "transaction_date desc, modified desc",
            "limit_page_length": 5,
        },
    )
    if sales_order_resp.status_code == 200:
        raw_orders = sales_order_resp.json().get("data", [])
        if isinstance(raw_orders, list):
            for row in raw_orders:
                if isinstance(row, dict):
                    sales_orders.append(
                        {
                            "name": row.get("name"),
                            "transaction_date": row.get("transaction_date"),
                            "status": row.get("status"),
                            "grand_total": row.get("grand_total"),
                            "currency": row.get("currency"),
                        }
                    )

    sales_invoice_resp = request_tenant_erpnext(
        tenant,
        "GET",
        "/api/resource/Sales Invoice",
        params={
            "fields": json.dumps(["name", "posting_date", "status", "grand_total", "currency"]),
            "filters": json.dumps([["customer", "=", erp_customer_id]]),
            "order_by": "posting_date desc, modified desc",
            "limit_page_length": 5,
        },
    )
    if sales_invoice_resp.status_code == 200:
        raw_invoices = sales_invoice_resp.json().get("data", [])
        if isinstance(raw_invoices, list):
            for row in raw_invoices:
                if isinstance(row, dict):
                    sales_invoices.append(
                        {
                            "name": row.get("name"),
                            "posting_date": row.get("posting_date"),
                            "status": row.get("status"),
                            "grand_total": row.get("grand_total"),
                            "currency": row.get("currency"),
                        }
                    )

    return sales_orders, sales_invoices


def resolve_buyer_by_phone_live(tenant, normalized_phone: str | None) -> dict[str, str | None] | None:
    if not normalized_phone:
        return None
    contacts: list[dict[str, Any]] = []
    for field_name in ("mobile_no", "phone"):
        if contacts:
            break
        for variant in phone_match_variants(normalized_phone):
            contact_resp = request_tenant_erpnext(
                tenant,
                "GET",
                "/api/resource/Contact",
                params={
                    "filters": json.dumps([[field_name, "=", variant]]),
                    "fields": json.dumps(["name", "full_name", "mobile_no", "phone"]),
                    "limit_page_length": 5,
                },
            )
            if contact_resp.status_code != 200:
                continue
            payload = contact_resp.json().get("data", [])
            if isinstance(payload, list) and payload:
                contacts = payload
                break
    if not contacts:
        return None
    contact_name = contacts[0]["name"]
    contact_detail_resp = request_tenant_erpnext(
        tenant,
        "GET",
        f"/api/resource/Contact/{quote(str(contact_name or '').strip(), safe='')}",
    )
    customer_name = None
    contact_full_name = contacts[0].get("full_name")
    if contact_detail_resp.status_code == 200:
        contact_doc = contact_detail_resp.json().get("data", {})
        if isinstance(contact_doc, dict):
            links = contact_doc.get("links")
            if isinstance(links, list):
                for row in links:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("link_doctype") or "").strip() != "Customer":
                        continue
                    customer_name = str(row.get("link_name") or "").strip() or None
                    if customer_name:
                        break
            contact_full_name = contact_doc.get("full_name") or contact_full_name

    if not customer_name:
        link_resp = request_tenant_erpnext(
            tenant,
            "GET",
            "/api/resource/Dynamic Link",
            params={
                "filters": json.dumps(
                    [
                        ["link_doctype", "=", "Customer"],
                        ["parenttype", "=", "Contact"],
                        ["parent", "=", contact_name],
                    ]
                ),
                "fields": json.dumps(["link_name"]),
                "limit_page_length": 1,
            },
        )
        if link_resp.status_code == 200 and link_resp.json().get("data"):
            customer_name = link_resp.json()["data"][0]["link_name"]

    if not customer_name:
        return {
            "erp_contact_id": contact_name,
            "contact_name": contact_full_name,
            "erp_customer_id": None,
            "erp_customer_name": None,
        }
    customer = get_customer_detail(tenant, customer_name, fields=json.dumps(["name", "customer_name"]))
    return {
        "erp_contact_id": contact_name,
        "contact_name": contact_full_name,
        "erp_customer_id": customer_name,
        "erp_customer_name": (customer or {}).get("customer_name"),
    }


def resolve_buyer_by_phone(db: Session, tenant, normalized_phone: str | None) -> dict[str, str | None] | None:
    snapshot_match = resolve_buyer_from_snapshots(db, tenant, normalized_phone)
    if snapshot_match:
        return snapshot_match
    return resolve_buyer_by_phone_live(tenant, normalized_phone)


def resolve_customer_by_phone(tenant, normalized_phone: str | None) -> tuple[str | None, str | None]:
    match = resolve_buyer_by_phone_live(tenant, normalized_phone)
    if not match:
        return None, None
    return match.get("erp_customer_id"), match.get("contact_name")


def create_individual_customer(tenant, *, full_name: str, normalized_phone: str | None = None) -> tuple[str, str | None]:
    customer_body: dict[str, Any] = {
        "customer_name": full_name,
        "customer_type": "Individual",
        "customer_group": "Individual",
        "territory": "All Territories",
    }
    customer_resp = request_tenant_erpnext(tenant, "POST", "/api/resource/Customer", json_body=customer_body)
    if customer_resp.status_code not in (200, 201):
        raise ValueError(f"ERPNext Customer creation failed: {customer_resp.text[:200]}")
    customer_doc = customer_resp.json().get("data", {})
    customer_id = str(customer_doc.get("name") or "").strip()

    if normalized_phone:
        contact_body = {
            "full_name": full_name,
            "mobile_no": normalized_phone,
            "links": [{"link_doctype": "Customer", "link_name": customer_id}],
        }
        request_tenant_erpnext(tenant, "POST", "/api/resource/Contact", json_body=contact_body)

    return customer_id, customer_doc.get("customer_name") or full_name
