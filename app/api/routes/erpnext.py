from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session
from urllib.parse import quote

from app.api.deps import get_db, get_erp_request_context, require_permissions
from app.services.allowlist import Allowlist, get_allowlist, normalize_doctype, normalize_method
from app.services.erpnext import ERPNextError, default_fields, request_erpnext
from app.services.idempotency import build_request_hash, extract_idempotency_key, get_replay_if_match, store_response
from app.services.permissions import (
    PERMISSION_CUSTOMERS_READ,
    PERMISSION_ITEMS_READ,
    PERMISSION_PICKLISTS_READ,
    PERMISSION_PICKLISTS_WRITE,
    PERMISSION_PURCHASE_ORDERS_READ,
    PERMISSION_RESOURCE_READ,
    PERMISSION_RESOURCE_WRITE,
    PERMISSION_SALES_ORDERS_READ,
    PERMISSION_SALES_ORDERS_WRITE,
    PERMISSION_STOCK_READ,
    PERMISSION_TRANSLATIONS_READ,
    PERMISSION_WAREHOUSES_READ,
)

router = APIRouter(tags=["erpnext"])


def resolve_fields(requested: str | None, fallback: list[str]) -> str:
    return requested if requested else default_fields(fallback)


def get_allowlist_dep(db: Session = Depends(get_db)) -> Allowlist:
    return get_allowlist(db)


def get_allowed_doctype(doctype: str, allowlist: Allowlist) -> str:
    if not allowlist.doctypes:
        raise HTTPException(status_code=503, detail="ERPNext proxy disabled")
    key = normalize_doctype(doctype).lower()
    if key not in allowlist.doctypes:
        raise HTTPException(status_code=403, detail="Doctype not allowed")
    return allowlist.doctypes[key]


def ensure_method_allowed(method: str, allowlist: Allowlist) -> str:
    if not allowlist.methods:
        raise HTTPException(status_code=503, detail="ERPNext proxy disabled")
    upper = normalize_method(method)
    if upper not in allowlist.methods:
        raise HTTPException(status_code=405, detail="Method not allowed")
    return upper


def extract_params(request: Request) -> dict[str, str] | None:
    if not request.query_params:
        return None
    return dict(request.query_params)


def build_proxy_response(content: bytes | str, status_code: int, content_type: str | None, replayed: bool = False) -> Response:
    headers = {"X-Idempotency-Replayed": "1"} if replayed else None
    return Response(content=content, status_code=status_code, media_type=content_type, headers=headers)


@router.get("/picklists")
def get_picklists(
    filters: str | None = Query(default=None),
    fields: str | None = Query(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_PICKLISTS_READ)
    params = {
        "fields": resolve_fields(fields, ["name", "status", "customer_name", "creation"]),
        "limit_page_length": 999,
    }
    if filters:
        params["filters"] = filters

    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Pick List", allowlist)

    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            "/api/resource/Pick List",
            params=params,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.get("/picklists/{name}")
def get_picklist(
    name: str,
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_PICKLISTS_READ)
    safe_name = quote(name, safe="")
    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Pick List", allowlist)
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            f"/api/resource/Pick List/{safe_name}",
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.put("/picklists/{name}")
def update_picklist(
    name: str,
    payload: dict = Body(...),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_PICKLISTS_WRITE)
    safe_name = quote(name, safe="")
    ensure_method_allowed("PUT", allowlist)
    get_allowed_doctype("Pick List", allowlist)
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "PUT",
            f"/api/resource/Pick List/{safe_name}",
            json_body=payload,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.get("/items/by-product-code")
def get_items_by_product_code(
    filters: str = Query(...),
    fields: str | None = Query(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_ITEMS_READ)
    params = {
        "filters": filters,
        "fields": resolve_fields(fields, ["item_code", "custom_product_code"]),
    }
    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Item", allowlist)
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            "/api/resource/Item",
            params=params,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.get("/items/all")
def get_items_all(
    limit_start: int | None = Query(default=None, ge=0),
    limit_page_length: int | None = Query(default=None, ge=1, le=2000),
    fields: str | None = Query(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_ITEMS_READ)
    params = {
        "fields": resolve_fields(fields, ["item_code", "item_name", "custom_product_code"]),
    }
    if limit_start is not None:
        params["limit_start"] = limit_start
    if limit_page_length is not None:
        params["limit_page_length"] = limit_page_length

    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Item", allowlist)

    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            "/api/resource/Item",
            params=params,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.get("/items/{item_code}")
def get_item(
    item_code: str,
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_ITEMS_READ)
    safe_code = quote(item_code, safe="")
    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Item", allowlist)
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            f"/api/resource/Item/{safe_code}",
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.get("/files/{file_path:path}")
def get_public_file(
    file_path: str,
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_ITEMS_READ)
    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Item", allowlist)
    normalized = (file_path or "").strip().lstrip("/")
    if not normalized:
        raise HTTPException(status_code=400, detail="File path is required")
    safe_path = quote(normalized, safe="/")
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            f"/files/{safe_path}",
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return build_proxy_response(response.content, response.status_code, response.headers.get("content-type"))


@router.get("/private/files/{file_path:path}")
def get_private_file(
    file_path: str,
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_ITEMS_READ)
    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Item", allowlist)
    normalized = (file_path or "").strip().lstrip("/")
    if not normalized:
        raise HTTPException(status_code=400, detail="File path is required")
    safe_path = quote(normalized, safe="/")
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            f"/private/files/{safe_path}",
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return build_proxy_response(response.content, response.status_code, response.headers.get("content-type"))


@router.get("/bin")
def get_bin(
    filters: str = Query(...),
    fields: str | None = Query(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_STOCK_READ)
    params = {
        "filters": filters,
        "fields": resolve_fields(
            fields,
            [
                "actual_qty",
                "reserved_qty",
                "reserved_qty_for_production",
                "reserved_qty_for_sub_contract",
            ],
        ),
        "limit_page_length": 1,
    }
    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Bin", allowlist)
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            "/api/resource/Bin",
            params=params,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.get("/purchase-orders")
def get_purchase_orders(
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_PURCHASE_ORDERS_READ)
    params = {
        "fields": default_fields(["name", "supplier"]),
        "limit_page_length": 999,
    }
    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Purchase Order", allowlist)
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            "/api/resource/Purchase Order",
            params=params,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.get("/purchase-orders/{name}")
def get_purchase_order(
    name: str,
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_PURCHASE_ORDERS_READ)
    safe_name = quote(name, safe="")
    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Purchase Order", allowlist)
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            f"/api/resource/Purchase Order/{safe_name}",
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.post("/picklists")
def create_picklist(
    payload: dict = Body(...),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_PICKLISTS_WRITE)
    ensure_method_allowed("POST", allowlist)
    get_allowed_doctype("Pick List", allowlist)
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "POST",
            "/api/resource/Pick List",
            json_body=payload,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.get("/sales-orders")
def get_sales_orders(
    filters: str | None = Query(default=None),
    fields: str | None = Query(default=None),
    limit_start: int | None = Query(default=None, ge=0),
    limit_page_length: int | None = Query(default=50, ge=1, le=2000),
    order_by: str | None = Query(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_SALES_ORDERS_READ)
    params = {
        "fields": resolve_fields(
            fields,
            [
                "name",
                "customer",
                "customer_name",
                "transaction_date",
                "delivery_date",
                "status",
                "docstatus",
                "modified",
                "grand_total",
                "currency",
            ],
        ),
        "limit_page_length": limit_page_length,
    }
    if filters:
        params["filters"] = filters
    if limit_start is not None:
        params["limit_start"] = limit_start
    if order_by:
        params["order_by"] = order_by

    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Sales Order", allowlist)

    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            "/api/resource/Sales Order",
            params=params,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return build_proxy_response(response.content, response.status_code, response.headers.get("content-type"))


@router.get("/sales-orders/{name}")
def get_sales_order(
    name: str,
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_SALES_ORDERS_READ)
    safe_name = quote(name, safe="")
    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Sales Order", allowlist)

    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            f"/api/resource/Sales Order/{safe_name}",
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return build_proxy_response(response.content, response.status_code, response.headers.get("content-type"))


@router.post("/sales-orders")
def create_sales_order(
    request: Request,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_SALES_ORDERS_WRITE)
    ensure_method_allowed("POST", allowlist)
    get_allowed_doctype("Sales Order", allowlist)

    idempotency_key = extract_idempotency_key(request)
    endpoint = "/sales-orders"
    request_hash = build_request_hash(payload) if idempotency_key else None

    if idempotency_key and request_hash:
        replay = get_replay_if_match(db, context.tenant.id, "POST", endpoint, idempotency_key, request_hash)
        if replay:
            return build_proxy_response(replay.body, replay.status_code, replay.content_type, replayed=True)

    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "POST",
            "/api/resource/Sales Order",
            json_body=payload,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if idempotency_key and request_hash:
        concurrent_replay = store_response(
            db,
            context.tenant.id,
            "POST",
            endpoint,
            idempotency_key,
            request_hash,
            response.status_code,
            response.headers.get("content-type"),
            response.content,
        )
        if concurrent_replay:
            return build_proxy_response(
                concurrent_replay.body,
                concurrent_replay.status_code,
                concurrent_replay.content_type,
                replayed=True,
            )

    return build_proxy_response(response.content, response.status_code, response.headers.get("content-type"))


@router.put("/sales-orders/{name}")
def update_sales_order(
    name: str,
    request: Request,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_SALES_ORDERS_WRITE)
    safe_name = quote(name, safe="")
    ensure_method_allowed("PUT", allowlist)
    get_allowed_doctype("Sales Order", allowlist)

    idempotency_key = extract_idempotency_key(request)
    endpoint = f"/sales-orders/{safe_name}"
    request_hash = build_request_hash(payload) if idempotency_key else None

    if idempotency_key and request_hash:
        replay = get_replay_if_match(db, context.tenant.id, "PUT", endpoint, idempotency_key, request_hash)
        if replay:
            return build_proxy_response(replay.body, replay.status_code, replay.content_type, replayed=True)

    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "PUT",
            f"/api/resource/Sales Order/{safe_name}",
            json_body=payload,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if idempotency_key and request_hash:
        concurrent_replay = store_response(
            db,
            context.tenant.id,
            "PUT",
            endpoint,
            idempotency_key,
            request_hash,
            response.status_code,
            response.headers.get("content-type"),
            response.content,
        )
        if concurrent_replay:
            return build_proxy_response(
                concurrent_replay.body,
                concurrent_replay.status_code,
                concurrent_replay.content_type,
                replayed=True,
            )

    return build_proxy_response(response.content, response.status_code, response.headers.get("content-type"))


@router.get("/stock-settings")
def get_stock_settings(
    fields: str | None = Query(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_STOCK_READ)
    params = {"fields": resolve_fields(fields, ["default_warehouse"])}
    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Stock Settings", allowlist)
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            "/api/resource/Stock Settings/Stock Settings",
            params=params,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.get("/warehouses")
def get_warehouses(
    limit_page_length: int | None = Query(default=None, ge=1, le=2000),
    fields: str | None = Query(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_WAREHOUSES_READ)
    params = {"fields": resolve_fields(fields, ["name"])}
    if limit_page_length is not None:
        params["limit_page_length"] = limit_page_length
    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Warehouse", allowlist)
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            "/api/resource/Warehouse",
            params=params,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.get("/customers")
def get_customers(
    limit_start: int | None = Query(default=None, ge=0),
    limit_page_length: int | None = Query(default=None, ge=1, le=2000),
    fields: str | None = Query(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_CUSTOMERS_READ)
    params = {"fields": resolve_fields(fields, ["name", "customer_name"])}
    if limit_start is not None:
        params["limit_start"] = limit_start
    if limit_page_length is not None:
        params["limit_page_length"] = limit_page_length
    ensure_method_allowed("GET", allowlist)
    get_allowed_doctype("Customer", allowlist)
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            "GET",
            "/api/resource/Customer",
            params=params,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.api_route("/resource/{doctype}", methods=["GET", "POST"])
def proxy_resource_collection(
    doctype: str,
    request: Request,
    payload: dict | None = Body(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    method = ensure_method_allowed(request.method, allowlist)
    allowed_doctype = get_allowed_doctype(doctype, allowlist)
    if method == "GET":
        if normalize_doctype(allowed_doctype).lower() == "translation":
            require_permissions(context, PERMISSION_RESOURCE_READ, PERMISSION_TRANSLATIONS_READ)
        else:
            require_permissions(context, PERMISSION_RESOURCE_READ)
    else:
        require_permissions(context, PERMISSION_RESOURCE_WRITE)

    safe_doctype = quote(allowed_doctype, safe="")
    params = extract_params(request)
    json_body = payload if method in {"POST", "PUT", "PATCH"} else None
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            method,
            f"/api/resource/{safe_doctype}",
            params=params,
            json_body=json_body,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.api_route("/resource/{doctype}/{name}", methods=["GET", "PUT", "PATCH", "DELETE"])
def proxy_resource_item(
    doctype: str,
    name: str,
    request: Request,
    payload: dict | None = Body(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    method = ensure_method_allowed(request.method, allowlist)
    allowed_doctype = get_allowed_doctype(doctype, allowlist)
    if method == "GET":
        if normalize_doctype(allowed_doctype).lower() == "translation":
            require_permissions(context, PERMISSION_RESOURCE_READ, PERMISSION_TRANSLATIONS_READ)
        else:
            require_permissions(context, PERMISSION_RESOURCE_READ)
    else:
        require_permissions(context, PERMISSION_RESOURCE_WRITE)

    safe_doctype = quote(allowed_doctype, safe="")
    safe_name = quote(name, safe="")
    params = extract_params(request)
    json_body = payload if method in {"POST", "PUT", "PATCH"} else None
    try:
        response = request_erpnext(
            context.tenant.erpnext_url,
            context.tenant.api_key,
            context.tenant.api_secret,
            method,
            f"/api/resource/{safe_doctype}/{safe_name}",
            params=params,
            json_body=json_body,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))
