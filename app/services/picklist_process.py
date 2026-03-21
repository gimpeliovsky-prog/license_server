import math
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.models import PickListDeliveryNoteLink, SalesOrderPickListLink
from app.models import Tenant
from app.services.erpnext import ERPNextError, request_erpnext


CREATE_PICK_LIST_METHOD_PATH = "/api/method/erpnext.selling.doctype.sales_order.sales_order.create_pick_list"
CREATE_DELIVERY_NOTE_METHOD_PATH = "/api/method/erpnext.stock.doctype.pick_list.pick_list.create_delivery_note"
SUBMIT_DOCUMENT_METHOD_PATH = "/api/method/frappe.client.submit"
ERP_INSERT_STRIP_FIELDS = {
    "name",
    "owner",
    "creation",
    "modified",
    "modified_by",
    "parent",
    "parentfield",
    "parenttype",
    "idx",
    "docstatus",
    "__islocal",
    "__unsaved",
    "__onload",
    "__last_sync_on",
    "_assign",
    "_comments",
    "_liked_by",
    "_user_tags",
}


class PickListProcessError(ERPNextError):
    def __init__(
        self,
        message: str,
        status_code: int = 502,
        *,
        reason_code: str = "erp_request_failed",
        retryable: bool = False,
        erp_refused: bool = True,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.reason_code = reason_code
        self.retryable = retryable
        self.erp_refused = erp_refused

    def to_detail(self) -> dict[str, object]:
        return {
            "message": str(self),
            "reason_code": self.reason_code,
            "retryable": self.retryable,
            "erp_refused": self.erp_refused,
        }


@dataclass(frozen=True)
class PickListShortage:
    item_code: str
    item_name: str
    requested_qty: float
    allocated_qty: float
    shortage_qty: float


@dataclass(frozen=True)
class PickListPreview:
    sales_order_name: str
    draft_doc: dict[str, Any]
    shortages: list[PickListShortage]
    allocated_line_count: int


def get_linked_pick_list_name(db: Session, tenant: Tenant, sales_order_name: str) -> str | None:
    normalized_name = sales_order_name.strip()
    if not normalized_name:
        return None

    links = (
        db.query(SalesOrderPickListLink)
        .filter(
            SalesOrderPickListLink.tenant_id == tenant.id,
            SalesOrderPickListLink.sales_order_name == normalized_name,
        )
        .order_by(SalesOrderPickListLink.updated_at.desc(), SalesOrderPickListLink.created_at.desc())
        .all()
    )
    for link in links:
        if is_pick_list_link_active(tenant, link.pick_list_name):
            return link.pick_list_name
    return None


def remember_pick_list_link(db: Session, tenant: Tenant, sales_order_name: str, pick_list_name: str) -> None:
    normalized_sales_order = sales_order_name.strip()
    normalized_pick_list = pick_list_name.strip()
    if not normalized_sales_order or not normalized_pick_list:
        return

    link = (
        db.query(SalesOrderPickListLink)
        .filter(
            SalesOrderPickListLink.tenant_id == tenant.id,
            SalesOrderPickListLink.pick_list_name == normalized_pick_list,
        )
        .first()
    )
    if link is None:
        db.add(
            SalesOrderPickListLink(
                tenant_id=tenant.id,
                sales_order_name=normalized_sales_order,
                pick_list_name=normalized_pick_list,
            )
        )
    else:
        link.pick_list_name = normalized_pick_list
    db.flush()


def is_pick_list_link_active(tenant: Tenant, pick_list_name: str) -> bool:
    normalized_name = pick_list_name.strip()
    if not normalized_name:
        return False

    response = request_erpnext(
        tenant.erpnext_url,
        tenant.api_key,
        tenant.api_secret,
        "GET",
        f"/api/resource/Pick List/{quote(normalized_name, safe='')}",
    )
    if response.status_code == 404:
        return False
    if response.status_code >= 400:
        raise build_picklist_process_error(
            extract_response_detail(response, "Failed to fetch linked Pick List"),
            response.status_code,
        )

    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise PickListProcessError(
            "ERPNext returned invalid Pick List details response",
            status_code=502,
            reason_code="invalid_erp_response",
            retryable=True,
            erp_refused=False,
        )
    if _as_int(data.get("docstatus")) == 2:
        return False
    delivery_status = (_as_string(data.get("delivery_status")) or "").lower()
    status = (_as_string(data.get("status")) or "").lower()
    if delivery_status in {"delivered", "closed", "completed"}:
        return False
    if status in {"closed", "completed"}:
        return False
    return True


def get_linked_delivery_note_name(db: Session, tenant: Tenant, pick_list_name: str) -> str | None:
    normalized_name = pick_list_name.strip()
    if not normalized_name:
        return None

    link = (
        db.query(PickListDeliveryNoteLink)
        .filter(
            PickListDeliveryNoteLink.tenant_id == tenant.id,
            PickListDeliveryNoteLink.pick_list_name == normalized_name,
        )
        .first()
    )
    if not link:
        return None
    if not is_delivery_note_link_active(tenant, link.delivery_note_name):
        db.delete(link)
        db.flush()
        return None
    return link.delivery_note_name


def remember_delivery_note_link(db: Session, tenant: Tenant, pick_list_name: str, delivery_note_name: str) -> None:
    normalized_pick_list = pick_list_name.strip()
    normalized_delivery_note = delivery_note_name.strip()
    if not normalized_pick_list or not normalized_delivery_note:
        return

    link = (
        db.query(PickListDeliveryNoteLink)
        .filter(
            PickListDeliveryNoteLink.tenant_id == tenant.id,
            PickListDeliveryNoteLink.pick_list_name == normalized_pick_list,
        )
        .first()
    )
    if link is None:
        db.add(
            PickListDeliveryNoteLink(
                tenant_id=tenant.id,
                pick_list_name=normalized_pick_list,
                delivery_note_name=normalized_delivery_note,
            )
        )
    else:
        link.delivery_note_name = normalized_delivery_note
    db.flush()


def is_delivery_note_link_active(tenant: Tenant, delivery_note_name: str) -> bool:
    normalized_name = delivery_note_name.strip()
    if not normalized_name:
        return False

    response = request_erpnext(
        tenant.erpnext_url,
        tenant.api_key,
        tenant.api_secret,
        "GET",
        f"/api/resource/Delivery Note/{quote(normalized_name, safe='')}",
    )
    if response.status_code == 404:
        return False
    if response.status_code >= 400:
        raise build_picklist_process_error(
            extract_response_detail(response, "Failed to fetch linked Delivery Note"),
            response.status_code,
        )

    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise PickListProcessError(
            "ERPNext returned invalid Delivery Note details response",
            status_code=502,
            reason_code="invalid_erp_response",
            retryable=True,
            erp_refused=False,
        )
    return _as_int(data.get("docstatus")) != 2


def preview_pick_list_from_sales_order(tenant: Tenant, sales_order_name: str) -> PickListPreview:
    normalized_name = sales_order_name.strip()
    if not normalized_name:
        raise PickListProcessError("Sales Order is required", status_code=400, reason_code="sales_order_required")
    sales_order_doc = fetch_sales_order_details(tenant, normalized_name)
    if _as_int(sales_order_doc.get("docstatus")) != 1:
        raise PickListProcessError(
            "Sales Order must be submitted in ERPNext before Pick List creation.",
            status_code=409,
            reason_code="source_not_submitted",
            retryable=False,
            erp_refused=True,
        )
    draft_doc = request_pick_list_draft(tenant, normalized_name)
    return build_pick_list_preview(sales_order_doc, draft_doc, normalized_name)


def create_pick_list_from_preview(tenant: Tenant, preview: PickListPreview) -> str:
    payload = sanitize_for_insert(preview.draft_doc)
    if not isinstance(payload, dict) or not payload:
        raise PickListProcessError(
            "ERPNext returned an empty Pick List draft",
            status_code=502,
            reason_code="invalid_erp_response",
            retryable=True,
            erp_refused=False,
        )

    response = request_erpnext(
        tenant.erpnext_url,
        tenant.api_key,
        tenant.api_secret,
        "POST",
        "/api/resource/Pick List",
        json_body=payload,
    )
    if response.status_code >= 400:
        raise build_picklist_process_error(
            extract_response_detail(response, "Failed to create Pick List from Sales Order"),
            response.status_code,
        )

    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise PickListProcessError(
            "ERPNext returned invalid Pick List create response",
            status_code=502,
            reason_code="invalid_erp_response",
            retryable=True,
            erp_refused=False,
        )
    pick_list_name = _as_string(data.get("name"))
    if not pick_list_name:
        raise PickListProcessError(
            "ERPNext did not return Pick List name",
            status_code=502,
            reason_code="invalid_erp_response",
            retryable=True,
            erp_refused=False,
        )
    return pick_list_name


def create_delivery_note_from_pick_list(tenant: Tenant, pick_list_name: str) -> str:
    pick_list_doc = ensure_document_submitted(tenant, "Pick List", pick_list_name)
    draft_doc = request_delivery_note_draft(tenant, pick_list_name)
    payload = sanitize_for_insert(draft_doc)
    if not isinstance(payload, dict) or not payload:
        raise PickListProcessError(
            "ERPNext returned an empty Delivery Note draft",
            status_code=502,
            reason_code="invalid_erp_response",
            retryable=True,
            erp_refused=False,
        )
    normalize_delivery_note_payload_quantities(pick_list_doc, draft_doc, payload)
    ensure_delivery_note_customer(tenant, pick_list_doc, draft_doc, payload)

    response = request_erpnext(
        tenant.erpnext_url,
        tenant.api_key,
        tenant.api_secret,
        "POST",
        "/api/resource/Delivery Note",
        json_body=payload,
    )
    if response.status_code >= 400:
        raise build_picklist_process_error(
            extract_response_detail(response, "Failed to create Delivery Note from Pick List"),
            response.status_code,
        )

    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise PickListProcessError(
            "ERPNext returned invalid Delivery Note create response",
            status_code=502,
            reason_code="invalid_erp_response",
            retryable=True,
            erp_refused=False,
        )
    delivery_note_name = _as_string(data.get("name"))
    if not delivery_note_name:
        raise PickListProcessError(
            "ERPNext did not return Delivery Note name",
            status_code=502,
            reason_code="invalid_erp_response",
            retryable=True,
            erp_refused=False,
        )
    return delivery_note_name


def ensure_delivery_note_customer(
    tenant: Tenant,
    pick_list_doc: dict[str, Any],
    delivery_note_draft: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    customer = (
        _as_string(payload.get("customer"))
        or _as_string(delivery_note_draft.get("customer"))
        or _as_string(pick_list_doc.get("customer"))
        or _resolve_customer_from_sales_order(tenant, delivery_note_draft, pick_list_doc)
    )
    if not customer:
        raise PickListProcessError(
            "Delivery Note draft is missing Customer, and no customer could be resolved from Pick List or Sales Order.",
            status_code=409,
            reason_code="missing_customer",
            retryable=False,
            erp_refused=True,
        )
    payload["customer"] = customer

    customer_name = (
        _as_string(payload.get("customer_name"))
        or _as_string(delivery_note_draft.get("customer_name"))
        or _as_string(pick_list_doc.get("customer_name"))
    )
    if not customer_name:
        sales_order = _resolve_sales_order_name(delivery_note_draft, pick_list_doc)
        if sales_order:
            sales_order_doc = fetch_sales_order_details(tenant, sales_order)
            customer_name = _as_string(sales_order_doc.get("customer_name"))
    if customer_name and not _as_string(payload.get("customer_name")):
        payload["customer_name"] = customer_name


def _resolve_customer_from_sales_order(
    tenant: Tenant,
    delivery_note_draft: dict[str, Any],
    pick_list_doc: dict[str, Any],
) -> str | None:
    sales_order = _resolve_sales_order_name(delivery_note_draft, pick_list_doc)
    if not sales_order:
        return None
    sales_order_doc = fetch_sales_order_details(tenant, sales_order)
    return _as_string(sales_order_doc.get("customer"))


def _resolve_sales_order_name(*documents: dict[str, Any]) -> str | None:
    for document in documents:
        direct = _as_string(document.get("sales_order"))
        if direct:
            return direct
        for child_key in ("items", "locations"):
            entries = document.get(child_key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                sales_order = _as_string(entry.get("sales_order"))
                if sales_order:
                    return sales_order
    return None


def ensure_document_submitted(tenant: Tenant, doctype: str, docname: str) -> dict[str, Any]:
    document = fetch_document(tenant, doctype, docname)
    if _as_int(document.get("docstatus")) == 1:
        return document

    response = request_erpnext(
        tenant.erpnext_url,
        tenant.api_key,
        tenant.api_secret,
        "POST",
        SUBMIT_DOCUMENT_METHOD_PATH,
        json_body={"doc": document},
    )
    if response.status_code >= 400:
        raise build_picklist_process_error(
            extract_response_detail(response, f"Failed to submit {doctype}"),
            response.status_code,
        )
    return fetch_document(tenant, doctype, docname)


def fetch_document(tenant: Tenant, doctype: str, docname: str) -> dict[str, Any]:
    safe_doctype = quote(doctype, safe="")
    safe_name = quote(docname, safe="")
    response = request_erpnext(
        tenant.erpnext_url,
        tenant.api_key,
        tenant.api_secret,
        "GET",
        f"/api/resource/{safe_doctype}/{safe_name}",
    )
    if response.status_code >= 400:
        raise build_picklist_process_error(
            extract_response_detail(response, f"Failed to load {doctype}"),
            response.status_code,
        )
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise PickListProcessError(
            f"ERPNext returned invalid {doctype} response",
            status_code=502,
            reason_code="invalid_erp_response",
            retryable=True,
            erp_refused=False,
        )
    return data


def fetch_sales_order_details(tenant: Tenant, sales_order_name: str) -> dict[str, Any]:
    safe_name = quote(sales_order_name, safe="")
    response = request_erpnext(
        tenant.erpnext_url,
        tenant.api_key,
        tenant.api_secret,
        "GET",
        f"/api/resource/Sales Order/{safe_name}",
    )
    if response.status_code >= 400:
        raise build_picklist_process_error(
            extract_response_detail(response, "Failed to load Sales Order details"),
            response.status_code,
        )
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise PickListProcessError(
            "ERPNext returned invalid Sales Order response",
            status_code=502,
            reason_code="invalid_erp_response",
            retryable=True,
            erp_refused=False,
        )
    return data


def request_pick_list_draft(tenant: Tenant, sales_order_name: str) -> dict[str, Any]:
    post_response = request_erpnext(
        tenant.erpnext_url,
        tenant.api_key,
        tenant.api_secret,
        "POST",
        CREATE_PICK_LIST_METHOD_PATH,
        json_body={"source_name": sales_order_name},
    )
    response = post_response
    if post_response.status_code in {404, 405}:
        response = request_erpnext(
            tenant.erpnext_url,
            tenant.api_key,
            tenant.api_secret,
            "GET",
            CREATE_PICK_LIST_METHOD_PATH,
            params={"source_name": sales_order_name},
        )

    if response.status_code >= 400:
        detail = extract_response_detail(response, "Failed to generate Pick List from Sales Order")
        normalized = detail.lower()
        unsupported = (
            "allowlist" in normalized
            or "not whitelisted" in normalized
            or "not permitted" in normalized
            or "permissionerror" in normalized
            or ("method" in normalized and "not found" in normalized)
            or "has no attribute" in normalized
            or "does not exist" in normalized
        )
        if unsupported:
            raise PickListProcessError(
                "ERPNext cannot create a Pick List from Sales Order. Check method allowlist and user permissions.",
                status_code=501,
                reason_code="unsupported_configuration",
            )
        raise build_picklist_process_error(detail, response.status_code)

    payload = response.json()
    message = payload.get("message") if isinstance(payload, dict) else None
    if isinstance(message, dict):
        return message
    if isinstance(message, str):
        parsed = _try_parse_json_object(message)
        if parsed is not None:
            return parsed
    raise PickListProcessError(
        "ERPNext did not return a Pick List draft",
        status_code=502,
        reason_code="invalid_erp_response",
        retryable=True,
        erp_refused=False,
    )


def request_delivery_note_draft(tenant: Tenant, pick_list_name: str) -> dict[str, Any]:
    post_response = request_erpnext(
        tenant.erpnext_url,
        tenant.api_key,
        tenant.api_secret,
        "POST",
        CREATE_DELIVERY_NOTE_METHOD_PATH,
        json_body={"source_name": pick_list_name},
    )
    response = post_response
    if post_response.status_code in {404, 405}:
        response = request_erpnext(
            tenant.erpnext_url,
            tenant.api_key,
            tenant.api_secret,
            "GET",
            CREATE_DELIVERY_NOTE_METHOD_PATH,
            params={"source_name": pick_list_name},
        )

    if response.status_code >= 400:
        detail = extract_response_detail(response, "Failed to generate Delivery Note from Pick List")
        normalized = detail.lower()
        unsupported = (
            "allowlist" in normalized
            or "not whitelisted" in normalized
            or "not permitted" in normalized
            or "permissionerror" in normalized
            or ("method" in normalized and "not found" in normalized)
            or "has no attribute" in normalized
            or "does not exist" in normalized
        )
        if unsupported:
            raise PickListProcessError(
                "ERPNext cannot create a Delivery Note from Pick List. Check method allowlist and user permissions.",
                status_code=501,
                reason_code="unsupported_configuration",
            )
        raise build_picklist_process_error(detail, response.status_code)

    payload = response.json()
    message = payload.get("message") if isinstance(payload, dict) else None
    if isinstance(message, dict):
        return message
    if isinstance(message, str):
        parsed = _try_parse_json_object(message)
        if parsed is not None:
            return parsed
    raise PickListProcessError(
        "ERPNext did not return a Delivery Note draft",
        status_code=502,
        reason_code="invalid_erp_response",
        retryable=True,
        erp_refused=False,
    )


def build_pick_list_preview(
    sales_order_doc: dict[str, Any],
    draft_doc: dict[str, Any],
    sales_order_name: str,
) -> PickListPreview:
    allocated_qty_by_source: dict[str, float] = {}
    allocated_qty_by_item_code: dict[str, float] = {}
    allocated_line_count = 0

    locations = draft_doc.get("locations")
    if isinstance(locations, list):
        for entry in locations:
            if not isinstance(entry, dict):
                continue
            source_key = _as_string(entry.get("sales_order_item")) or _as_string(entry.get("product_bundle_item"))
            item_code = _as_string(entry.get("item_code")) or ""
            qty = _as_float(entry.get("qty"))
            if qty > 0:
                allocated_line_count += 1
            if source_key:
                allocated_qty_by_source[source_key] = allocated_qty_by_source.get(source_key, 0.0) + qty
            elif item_code:
                allocated_qty_by_item_code[item_code] = allocated_qty_by_item_code.get(item_code, 0.0) + qty

    item_fallback_consumption: dict[str, float] = {}
    shortages: list[PickListShortage] = []
    for line in sales_order_doc.get("items") or []:
        if not isinstance(line, dict):
            continue
        item_code = _as_string(line.get("item_code")) or ""
        if not item_code:
            continue
        if _as_int(line.get("delivered_by_supplier")) == 1:
            continue

        conversion_factor = _as_float(line.get("conversion_factor")) or 1.0
        if conversion_factor <= 0:
            conversion_factor = 1.0
        already_picked = _as_float(line.get("picked_qty")) / conversion_factor
        pending_qty = max(
            0.0,
            _as_float(line.get("qty")) - max(already_picked, _as_float(line.get("delivered_qty"))),
        )
        if pending_qty <= 0:
            continue

        line_name = _as_string(line.get("name"))
        if line_name:
            allocated_qty = allocated_qty_by_source.get(line_name, 0.0)
        else:
            total_for_item = allocated_qty_by_item_code.get(item_code, 0.0)
            used = item_fallback_consumption.get(item_code, 0.0)
            remaining = max(0.0, total_for_item - used)
            allocated_qty = min(remaining, pending_qty)
            item_fallback_consumption[item_code] = used + allocated_qty

        if allocated_qty + 1e-6 < pending_qty:
            shortages.append(
                PickListShortage(
                    item_code=item_code,
                    item_name=_as_string(line.get("item_name")) or item_code,
                    requested_qty=pending_qty,
                    allocated_qty=allocated_qty,
                    shortage_qty=max(0.0, pending_qty - allocated_qty),
                )
            )

    return PickListPreview(
        sales_order_name=sales_order_name,
        draft_doc=draft_doc,
        shortages=shortages,
        allocated_line_count=allocated_line_count,
    )


def sanitize_for_insert(value: Any) -> Any:
    if isinstance(value, list):
        return [sanitize_for_insert(item) for item in value]
    if not isinstance(value, dict):
        return value
    clean: dict[str, Any] = {}
    for key, child in value.items():
        if key in ERP_INSERT_STRIP_FIELDS:
            continue
        clean[key] = sanitize_for_insert(child)
    return clean


def normalize_delivery_note_payload_quantities(
    pick_list_doc: dict[str, Any],
    draft_doc: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    draft_items = draft_doc.get("items")
    payload_items = payload.get("items")
    pick_locations = pick_list_doc.get("locations")
    if not isinstance(draft_items, list) or not isinstance(payload_items, list) or not isinstance(pick_locations, list):
        return

    indexed_pick_lines: list[tuple[int, dict[str, Any]]] = [
        (index, line) for index, line in enumerate(pick_locations) if isinstance(line, dict)
    ]
    used_indexes: set[int] = set()

    for original_item, payload_item in zip(draft_items, payload_items):
        if not isinstance(original_item, dict) or not isinstance(payload_item, dict):
            continue
        matched = _match_pick_line_for_delivery_item(original_item, indexed_pick_lines, used_indexes)
        if matched is None:
            continue
        _, pick_line = matched
        _normalize_delivery_item_from_pick_line(payload_item, pick_line)


def _match_pick_line_for_delivery_item(
    draft_item: dict[str, Any],
    indexed_pick_lines: list[tuple[int, dict[str, Any]]],
    used_indexes: set[int],
) -> tuple[int, dict[str, Any]] | None:
    draft_pick_list_item = (
        _as_string(draft_item.get("pick_list_item"))
        or _as_string(draft_item.get("pick_list_item_name"))
        or _as_string(draft_item.get("against_pick_list_item"))
    )
    if draft_pick_list_item:
        for index, line in indexed_pick_lines:
            if index in used_indexes:
                continue
            if _as_string(line.get("name")) == draft_pick_list_item:
                used_indexes.add(index)
                return index, line

    source_key = _as_string(draft_item.get("sales_order_item")) or _as_string(draft_item.get("product_bundle_item"))
    if source_key:
        for index, line in indexed_pick_lines:
            if index in used_indexes:
                continue
            line_source_key = _as_string(line.get("sales_order_item")) or _as_string(line.get("product_bundle_item"))
            if line_source_key == source_key:
                used_indexes.add(index)
                return index, line

    item_code = _as_string(draft_item.get("item_code"))
    if item_code:
        for index, line in indexed_pick_lines:
            if index in used_indexes:
                continue
            if _as_string(line.get("item_code")) == item_code:
                used_indexes.add(index)
                return index, line

    return None


def _normalize_delivery_item_from_pick_line(payload_item: dict[str, Any], pick_line: dict[str, Any]) -> None:
    commercial_uom = _as_string(pick_line.get("uom")) or _as_string(payload_item.get("uom"))
    stock_uom = _as_string(pick_line.get("stock_uom")) or commercial_uom or _as_string(payload_item.get("stock_uom"))
    if not commercial_uom or not stock_uom:
        return
    if commercial_uom.lower() == stock_uom.lower():
        return
    if _is_weight_uom(commercial_uom) or not _is_weight_uom(stock_uom):
        return

    picked_stock_qty = _as_float(pick_line.get("picked_qty"))
    conversion_factor = _as_float(pick_line.get("conversion_factor")) or _as_float(payload_item.get("conversion_factor")) or 1.0
    if picked_stock_qty <= 0.0 or conversion_factor <= 0.0:
        return

    rounded_qty = _round_half_up_to_int(picked_stock_qty / conversion_factor)
    if rounded_qty <= 0:
        return

    payload_item["qty"] = float(rounded_qty)
    payload_item["uom"] = commercial_uom
    payload_item["stock_uom"] = stock_uom
    payload_item["stock_qty"] = picked_stock_qty
    payload_item["conversion_factor"] = picked_stock_qty / rounded_qty


def _is_weight_uom(uom: str | None) -> bool:
    normalized = (uom or "").strip().lower().replace('"', "").replace(" ", "")
    return normalized in {
        "kg",
        "kgs",
        "kilogram",
        "kilograms",
        "g",
        "gr",
        "gram",
        "grams",
        "ק\"ג",
        "קג",
    }


def _round_half_up_to_int(value: float) -> int:
    if value >= 0.0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def extract_response_detail(response: Any, fallback: str) -> str:
    def extract_text(value: Any) -> str | None:
        import json

        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.startswith("{") or text.startswith("["):
                try:
                    parsed = json.loads(text)
                except ValueError:
                    return text
                nested = extract_text(parsed)
                return nested or text
            return text
        if isinstance(value, list):
            parts = [extract_text(item) for item in value]
            combined = "; ".join(part for part in parts if part)
            return combined or None
        if isinstance(value, dict):
            for key in ("message", "msg", "title", "detail", "exception", "exc", "_server_messages"):
                nested = extract_text(value.get(key))
                if nested:
                    return nested
            parts: list[str] = []
            for key, item in value.items():
                if str(key).startswith("_"):
                    continue
                nested = extract_text(item)
                if nested:
                    parts.append(f"{key}: {nested}")
                if len(parts) >= 4:
                    break
            combined = "; ".join(parts)
            return combined or None
        return str(value).strip() or None

    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("detail", "_server_messages", "message", "exception", "exc", "_error_message", "exc_type"):
            detail = extract_text(payload.get(key))
            if detail:
                return detail
        detail = extract_text(payload)
        if detail:
            return detail
    text = getattr(response, "text", "") or ""
    text = text.strip()
    if text:
        return text
    status_code = getattr(response, "status_code", None)
    if status_code:
        return f"{fallback} [{status_code}]"
    return fallback


def map_erp_status(status_code: int) -> int:
    if status_code >= 500:
        return 502
    return status_code


def build_picklist_process_error(detail: str, status_code: int) -> PickListProcessError:
    reason_code, retryable, erp_refused = classify_erp_failure(detail, status_code)
    return PickListProcessError(
        detail,
        status_code=map_erp_status(status_code),
        reason_code=reason_code,
        retryable=retryable,
        erp_refused=erp_refused,
    )


def classify_erp_failure(detail: str, status_code: int) -> tuple[str, bool, bool]:
    normalized = detail.strip().lower()
    if status_code >= 500:
        return "erp_unavailable", True, False
    if any(token in normalized for token in ("insufficient stock", "not enough stock", "out of stock", "shortage")):
        return "stock_shortage", False, True
    if any(token in normalized for token in ("mandatory", "reqd", "required field", "missing value")):
        return "missing_required_fields", False, True
    if any(token in normalized for token in ("permissionerror", "not permitted", "no permission", "forbidden")):
        return "permission_denied", False, True
    if any(token in normalized for token in ("validationerror", "invalid", "cannot create", "must be", "should be")):
        return "erp_validation_failed", False, True
    if status_code in {400, 409, 422}:
        return "erp_refused", False, True
    return "erp_request_failed", status_code >= 500, status_code < 500


def _try_parse_json_object(value: str) -> dict[str, Any] | None:
    import json

    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _as_float(value: Any) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return 0.0
        try:
            return float(normalized)
        except ValueError:
            return 0.0
    return 0.0


def _as_int(value: Any) -> int:
    return int(_as_float(value))
