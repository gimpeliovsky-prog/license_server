from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from app.services.erp_catalog import resolve_item_doc
from app.services.erpnext import desk_form_url, printview_url, request_tenant_erpnext
from app.utils.time import utcnow


def normalize_sales_order_item(item: dict[str, Any]) -> dict[str, Any] | None:
    item_code = item.get("item_code")
    qty = item.get("qty")
    if not item_code or qty in (None, ""):
        return None

    normalized: dict[str, Any] = {
        "item_code": item_code,
        "qty": qty,
    }
    for optional_field in ("rate", "uom", "conversion_factor"):
        value = item.get(optional_field)
        if value not in (None, ""):
            normalized[optional_field] = value
    return normalized


def build_new_sales_order_item(item: dict[str, Any]) -> dict[str, Any] | None:
    normalized = normalize_sales_order_item(item)
    if not normalized:
        return None
    normalized["doctype"] = "Sales Order Item"
    normalized["parenttype"] = "Sales Order"
    normalized["parentfield"] = "items"
    return normalized


def sanitize_existing_sales_order_item(item: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {
        "doctype": "Sales Order Item",
        "name": item.get("name"),
        "parent": item.get("parent"),
        "parenttype": "Sales Order",
        "parentfield": "items",
        "docstatus": item.get("docstatus", 0),
        "idx": item.get("idx"),
        "item_code": item.get("item_code"),
        "item_name": item.get("item_name"),
        "description": item.get("description"),
        "qty": item.get("qty"),
        "rate": item.get("rate"),
        "amount": item.get("amount"),
        "uom": item.get("uom"),
        "stock_uom": item.get("stock_uom"),
        "conversion_factor": item.get("conversion_factor"),
        "warehouse": item.get("warehouse"),
        "delivery_date": item.get("delivery_date"),
    }
    return {key: value for key, value in cleaned.items() if value not in (None, "")}


def fetch_sales_order_doc(tenant, sales_order_name: str) -> dict[str, Any]:
    response = request_tenant_erpnext(
        tenant,
        "GET",
        f"/api/resource/Sales Order/{quote(sales_order_name, safe='')}",
    )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Failed to load Sales Order {sales_order_name}")
    order_doc = response.json().get("data", {})
    if not isinstance(order_doc, dict):
        raise HTTPException(status_code=502, detail="ERPNext returned invalid Sales Order payload")
    return order_doc


def build_sales_order_summary(tenant, order: dict[str, Any], sales_order_name: str | None = None, *, updated: bool = False) -> dict[str, Any]:
    order_name = order.get("name") or sales_order_name
    response = {
        "name": order_name,
        "status": order.get("status", "Draft"),
        "grand_total": order.get("grand_total"),
        "order_url": desk_form_url(tenant.erpnext_url, "sales-order", order_name),
        "order_print_url": printview_url(tenant.erpnext_url, "Sales Order", order_name),
    }
    if updated:
        response["updated"] = True
    return response


def create_sales_order(tenant, *, customer: str, delivery_date: str | None, items: list[dict[str, Any]]) -> dict[str, Any]:
    resolved_delivery_date = delivery_date or utcnow().date().isoformat()
    order_items: list[dict[str, Any]] = []
    for item in items:
        item_doc = resolve_item_doc(tenant, item.get("item_code"))
        order_item = {
            "item_code": (item_doc or {}).get("item_code") or item.get("item_code"),
            "qty": item.get("qty"),
        }
        for optional_field in ("rate", "uom", "conversion_factor"):
            value = item.get(optional_field)
            if value not in (None, ""):
                order_item[optional_field] = value
        order_items.append(order_item)

    response = request_tenant_erpnext(
        tenant,
        "POST",
        "/api/resource/Sales Order",
        json_body={
            "customer": customer,
            "delivery_date": resolved_delivery_date,
            "items": order_items,
            "order_type": "Sales",
        },
    )
    if response.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=response.json().get("exception", response.text[:200]))
    order = response.json().get("data", {})
    return build_sales_order_summary(tenant, order)


def update_sales_order_items(tenant, *, sales_order_name: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    def _build_save_doc(order_doc: dict[str, Any]) -> dict[str, Any]:
        merged_items: list[dict[str, Any]] = []
        for item in order_doc.get("items", []):
            if isinstance(item, dict):
                merged_items.append(sanitize_existing_sales_order_item(item))

        for item in items:
            item_doc = resolve_item_doc(tenant, item.get("item_code"))
            normalized_source = dict(item)
            if item_doc and item_doc.get("item_code"):
                normalized_source["item_code"] = item_doc.get("item_code")
            normalized_new = build_new_sales_order_item(normalized_source)
            if normalized_new:
                normalized_new["parent"] = order_doc.get("name") or sales_order_name
                normalized_new["docstatus"] = 0
                normalized_new["idx"] = len(merged_items) + 1
                merged_items.append(normalized_new)

        save_doc: dict[str, Any] = {
            "doctype": "Sales Order",
            "name": order_doc.get("name") or sales_order_name,
            "owner": order_doc.get("owner"),
            "creation": order_doc.get("creation"),
            "modified": order_doc.get("modified"),
            "naming_series": order_doc.get("naming_series"),
            "customer": order_doc.get("customer"),
            "delivery_date": order_doc.get("delivery_date"),
            "transaction_date": order_doc.get("transaction_date"),
            "company": order_doc.get("company"),
            "currency": order_doc.get("currency"),
            "selling_price_list": order_doc.get("selling_price_list"),
            "price_list_currency": order_doc.get("price_list_currency"),
            "plc_conversion_rate": order_doc.get("plc_conversion_rate"),
            "conversion_rate": order_doc.get("conversion_rate"),
            "order_type": order_doc.get("order_type") or "Sales",
            "items": merged_items,
        }
        return {key: value for key, value in save_doc.items() if value not in (None, "")}

    order_doc = fetch_sales_order_doc(tenant, sales_order_name)
    save_doc = _build_save_doc(order_doc)
    response = request_tenant_erpnext(tenant, "POST", "/api/method/frappe.client.save", json_body={"doc": save_doc})
    if response.status_code == 417:
        response = request_tenant_erpnext(
            tenant,
            "POST",
            "/api/method/frappe.client.save",
            json_body={"doc": json.dumps(save_doc, ensure_ascii=False, default=str)},
        )
    if response.status_code == 417 and "TimestampMismatchError" in response.text:
        order_doc = fetch_sales_order_doc(tenant, sales_order_name)
        save_doc = _build_save_doc(order_doc)
        response = request_tenant_erpnext(tenant, "POST", "/api/method/frappe.client.save", json_body={"doc": save_doc})
        if response.status_code == 417:
            response = request_tenant_erpnext(
                tenant,
                "POST",
                "/api/method/frappe.client.save",
                json_body={"doc": json.dumps(save_doc, ensure_ascii=False, default=str)},
            )
    if response.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=response.json().get("exception", response.text[:200]))

    payload = response.json()
    order = payload.get("message") or payload.get("data") or {}
    return build_sales_order_summary(tenant, order, sales_order_name, updated=True)


def create_invoice_from_sales_order(tenant, *, sales_order_name: str) -> dict[str, Any]:
    response = request_tenant_erpnext(
        tenant,
        "POST",
        "/api/method/erpnext.accounts.doctype.sales_invoice.sales_invoice.make_sales_invoice",
        json_body={"source_name": sales_order_name},
    )
    if response.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=response.json().get("exception", response.text[:200]))
    invoice = response.json().get("message", {})
    return {
        "name": invoice.get("name"),
        "grand_total": invoice.get("grand_total"),
        "currency": invoice.get("currency"),
        "status": invoice.get("status", "Draft"),
        "due_date": invoice.get("due_date"),
        "invoice_url": desk_form_url(tenant.erpnext_url, "sales-invoice", invoice.get("name")),
    }

