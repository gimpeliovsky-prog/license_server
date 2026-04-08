from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from app.services.erpnext import request_tenant_erpnext


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


def resolve_customer_by_phone(tenant, normalized_phone: str | None) -> tuple[str | None, str | None]:
    if not normalized_phone:
        return None, None
    contact_resp = request_tenant_erpnext(
        tenant,
        "GET",
        "/api/resource/Contact",
        params={
            "filters": json.dumps([["mobile_no", "=", normalized_phone]]),
            "fields": json.dumps(["name", "full_name", "mobile_no"]),
            "limit_page_length": 5,
        },
    )
    if contact_resp.status_code != 200:
        return None, None
    contacts = contact_resp.json().get("data", [])
    if not contacts:
        return None, None
    contact_name = contacts[0]["name"]
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
    if link_resp.status_code != 200 or not link_resp.json().get("data"):
        return None, None
    customer_name = link_resp.json()["data"][0]["link_name"]
    return customer_name, contacts[0].get("full_name")


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
