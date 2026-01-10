from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session
from urllib.parse import quote

from app.api.deps import get_db, get_request_context
from app.services.allowlist import Allowlist, get_allowlist, normalize_doctype, normalize_method
from app.services.erpnext import ERPNextError, default_fields, request_erpnext

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


@router.get("/picklists")
def get_picklists(
    filters: str | None = Query(default=None),
    fields: str | None = Query(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_request_context),
):
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
    context=Depends(get_request_context),
):
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
    context=Depends(get_request_context),
):
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


@router.get("/items/{item_code}")
def get_item(
    item_code: str,
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_request_context),
):
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


@router.get("/items/by-product-code")
def get_items_by_product_code(
    filters: str = Query(...),
    fields: str | None = Query(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_request_context),
):
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
    context=Depends(get_request_context),
):
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


@router.get("/bin")
def get_bin(
    filters: str = Query(...),
    fields: str | None = Query(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_request_context),
):
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
    context=Depends(get_request_context),
):
    params = {
        "fields": default_fields(["*"]),
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


@router.post("/picklists")
def create_picklist(
    payload: dict = Body(...),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_request_context),
):
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


@router.get("/stock-settings")
def get_stock_settings(
    fields: str | None = Query(default=None),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_request_context),
):
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
    context=Depends(get_request_context),
):
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
    context=Depends(get_request_context),
):
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
    context=Depends(get_request_context),
):
    method = ensure_method_allowed(request.method, allowlist)
    allowed_doctype = get_allowed_doctype(doctype, allowlist)
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
    context=Depends(get_request_context),
):
    method = ensure_method_allowed(request.method, allowlist)
    allowed_doctype = get_allowed_doctype(doctype, allowlist)
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
