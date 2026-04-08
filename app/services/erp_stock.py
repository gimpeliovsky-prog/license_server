from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from app.services.erp_sales import fetch_sales_order_doc
from app.services.erpnext import request_tenant_erpnext


def get_bin_records(
    tenant,
    *,
    filters: str,
    fields: str,
    limit_page_length: int = 1,
) -> list[dict[str, Any]]:
    response = request_tenant_erpnext(
        tenant,
        "GET",
        "/api/resource/Bin",
        params={
            "filters": filters,
            "fields": fields,
            "limit_page_length": limit_page_length,
        },
    )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to load Bin records")
    payload = response.json().get("data", [])
    return payload if isinstance(payload, list) else []


def get_stock_settings(tenant, *, fields: str) -> dict[str, Any]:
    response = request_tenant_erpnext(
        tenant,
        "GET",
        "/api/resource/Stock Settings/Stock Settings",
        params={"fields": fields},
    )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to load Stock Settings")
    payload = response.json().get("data", {})
    return payload if isinstance(payload, dict) else {}


def list_warehouses(
    tenant,
    *,
    fields: str,
    limit_page_length: int | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"fields": fields}
    if limit_page_length is not None:
        params["limit_page_length"] = limit_page_length
    response = request_tenant_erpnext(
        tenant,
        "GET",
        "/api/resource/Warehouse",
        params=params,
    )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to load warehouses")
    payload = response.json().get("data", [])
    return payload if isinstance(payload, list) else []


def build_sales_order_status(order: dict[str, Any], *, order_name: str | None = None) -> dict[str, Any]:
    data = order if isinstance(order, dict) else {}
    docstatus = data.get("docstatus")
    delivery_status = data.get("delivery_status")
    billing_status = data.get("billing_status")
    per_delivered = data.get("per_delivered")
    per_billed = data.get("per_billed")
    status_parts = " ".join(
        str(data.get(key) or "")
        for key in ["status", "docstatus", "delivery_status", "billing_status", "per_delivered", "per_billed"]
    ).casefold()
    delivered = "delivered" in status_parts or str(per_delivered or "") in {"100", "100.0"}
    invoiced = "invoiced" in status_parts or "completed" in status_parts or str(per_billed or "") in {"100", "100.0"}
    cancelled = "cancel" in status_parts or str(docstatus or "") == "2"
    can_modify = not (delivered or invoiced or cancelled)
    return {
        "name": data.get("name") or order_name,
        "status": data.get("status"),
        "docstatus": docstatus,
        "delivery_status": delivery_status,
        "billing_status": billing_status,
        "per_delivered": per_delivered,
        "per_billed": per_billed,
        "can_modify": can_modify,
        "items": data.get("items") if isinstance(data.get("items"), list) else [],
        "grand_total": data.get("grand_total"),
        "rounded_total": data.get("rounded_total"),
        "total": data.get("total"),
        "net_total": data.get("net_total"),
        "currency": data.get("currency") or data.get("company_currency"),
    }


def get_sales_order_status(tenant, sales_order_name: str) -> dict[str, Any]:
    order_doc = fetch_sales_order_doc(tenant, sales_order_name)
    return build_sales_order_status(order_doc, order_name=sales_order_name)
