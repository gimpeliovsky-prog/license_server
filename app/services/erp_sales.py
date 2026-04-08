from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from app.services.erp_catalog import resolve_item_doc
from app.services.erpnext import desk_form_url, printview_url, request_tenant_erpnext
from app.utils.time import utcnow


def _safe_json(response) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _status_text(data: dict[str, Any], key: str) -> str:
    return str(data.get(key) or "").strip().casefold()


def _percentage_complete(value: Any) -> bool:
    try:
        return float(value or 0) >= 100.0
    except (TypeError, ValueError):
        return False


def _compact_sales_order_item(item: dict[str, Any]) -> dict[str, Any]:
    data = item if isinstance(item, dict) else {}
    compact = {
        "name": data.get("name"),
        "item_code": data.get("item_code"),
        "item_name": data.get("item_name"),
        "qty": data.get("qty"),
        "uom": data.get("uom") or data.get("stock_uom"),
    }
    return {key: value for key, value in compact.items() if value not in (None, "", [])}


def _build_sales_order_status(order: dict[str, Any], *, sales_order_name: str | None = None) -> dict[str, Any]:
    data = order if isinstance(order, dict) else {}
    docstatus = data.get("docstatus")
    delivery_status = data.get("delivery_status")
    billing_status = data.get("billing_status")
    per_delivered = data.get("per_delivered")
    per_billed = data.get("per_billed")
    status_text = _status_text(data, "status")
    delivery_status_text = _status_text(data, "delivery_status")
    billing_status_text = _status_text(data, "billing_status")
    delivered = (
        delivery_status_text in {"delivered", "fully delivered"}
        or status_text in {"delivered", "fully delivered"}
        or _percentage_complete(per_delivered)
    )
    invoiced = (
        billing_status_text in {"invoiced", "fully billed", "billed"}
        or status_text in {"completed", "invoiced", "fully billed", "billed"}
        or _percentage_complete(per_billed)
    )
    cancelled = status_text == "cancelled" or str(docstatus or "") == "2"
    can_modify = not (delivered or invoiced or cancelled)
    compact_items = [
        _compact_sales_order_item(item)
        for item in (data.get("items") if isinstance(data.get("items"), list) else [])
        if isinstance(item, dict)
    ]
    return {
        "name": data.get("name") or sales_order_name,
        "status": data.get("status"),
        "docstatus": docstatus,
        "delivery_status": delivery_status,
        "billing_status": billing_status,
        "per_delivered": per_delivered,
        "per_billed": per_billed,
        "can_modify": can_modify,
        "items": compact_items,
        "grand_total": data.get("grand_total"),
        "rounded_total": data.get("rounded_total"),
        "total": data.get("total"),
        "net_total": data.get("net_total"),
        "currency": data.get("currency") or data.get("company_currency"),
    }


def _non_modifiable_detail(
    order: dict[str, Any],
    *,
    sales_order_name: str,
    message: str | None = None,
) -> dict[str, Any]:
    state = _build_sales_order_status(order, sales_order_name=sales_order_name)
    return {
        "error": message or "Sales order cannot be modified in its current state.",
        "error_code": "sales_order_not_modifiable",
        "sales_order_name": state.get("name") or sales_order_name,
        **state,
    }


def _raise_if_order_not_modifiable(order: dict[str, Any], *, sales_order_name: str) -> None:
    state = _build_sales_order_status(order, sales_order_name=sales_order_name)
    if not state.get("can_modify"):
        raise HTTPException(status_code=409, detail=_non_modifiable_detail(order, sales_order_name=sales_order_name))


def _looks_like_not_modifiable_failure(response) -> bool:
    payload = _safe_json(response)
    detail = payload.get("exception") or payload.get("message") or payload.get("detail") or response.text
    text = str(detail or "").casefold()
    return any(
        marker in text
        for marker in (
            "update after submit",
            "cannot edit",
            "not allowed to change",
            "submitted document",
            "docstatus",
            "not editable",
            "cancelled",
        )
    )


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


def _normalize_item_action(item: dict[str, Any]) -> str:
    action = str((item or {}).get("action") or "add").strip().casefold()
    return {
        "delete": "remove",
        "set_quantity": "update",
        "change_quantity": "update",
    }.get(action, action or "add")


def _find_existing_item_indexes(existing_items: list[dict[str, Any]], item: dict[str, Any]) -> list[int]:
    row_name = str(item.get("row_name") or item.get("sales_order_item_name") or "").strip()
    if row_name:
        return [index for index, row in enumerate(existing_items) if str(row.get("name") or "").strip() == row_name]
    item_code = str(item.get("item_code") or "").strip().casefold()
    if not item_code:
        return []
    return [
        index
        for index, row in enumerate(existing_items)
        if str(row.get("item_code") or "").strip().casefold() == item_code
    ]


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
            if not isinstance(item, dict):
                continue
            action = _normalize_item_action(item)
            matching_indexes = _find_existing_item_indexes(merged_items, item)

            if len(matching_indexes) > 1:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "Sales order contains multiple matching rows for this item.",
                        "error_code": "sales_order_item_ambiguous",
                        "sales_order_name": sales_order_name,
                        "item_code": item.get("item_code"),
                    },
                )

            if action == "remove":
                if not matching_indexes:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "Sales order does not contain the requested item to remove.",
                            "error_code": "sales_order_item_not_found",
                            "sales_order_name": sales_order_name,
                            "item_code": item.get("item_code"),
                            "row_name": item.get("row_name"),
                        },
                    )
                del merged_items[matching_indexes[0]]
                continue

            if action == "update":
                if not matching_indexes:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "Sales order does not contain the requested item to update.",
                            "error_code": "sales_order_item_not_found",
                            "sales_order_name": sales_order_name,
                            "item_code": item.get("item_code"),
                            "row_name": item.get("row_name"),
                        },
                    )
                target_item = merged_items[matching_indexes[0]]
                qty = item.get("qty")
                if qty in (None, ""):
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "Quantity is required when updating an existing sales order row.",
                            "error_code": "sales_order_update_qty_required",
                            "sales_order_name": sales_order_name,
                            "item_code": item.get("item_code"),
                        },
                    )
                target_item["qty"] = qty
                for optional_field in ("rate", "uom", "conversion_factor"):
                    value = item.get(optional_field)
                    if value not in (None, ""):
                        target_item[optional_field] = value
                continue

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

        for index, merged_item in enumerate(merged_items, start=1):
            merged_item["idx"] = index

        if not merged_items:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Sales order must contain at least one item.",
                    "error_code": "sales_order_empty",
                    "sales_order_name": sales_order_name,
                },
            )

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
    _raise_if_order_not_modifiable(order_doc, sales_order_name=sales_order_name)
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
        refreshed_order: dict[str, Any] | None = None
        try:
            refreshed_order = fetch_sales_order_doc(tenant, sales_order_name)
        except HTTPException:
            refreshed_order = None
        if refreshed_order is not None:
            refreshed_state = _build_sales_order_status(refreshed_order, sales_order_name=sales_order_name)
            if not refreshed_state.get("can_modify"):
                raise HTTPException(
                    status_code=409,
                    detail=_non_modifiable_detail(
                        refreshed_order,
                        sales_order_name=sales_order_name,
                        message="Sales order is no longer editable because its ERP status changed.",
                    ),
                )
        if _looks_like_not_modifiable_failure(response):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Sales order is no longer editable because its ERP status changed.",
                    "error_code": "sales_order_not_modifiable",
                    "sales_order_name": sales_order_name,
                },
            )
        payload = _safe_json(response)
        raise HTTPException(status_code=502, detail=payload.get("exception", response.text[:200]))

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
