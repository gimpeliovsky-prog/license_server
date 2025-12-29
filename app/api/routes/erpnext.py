from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from urllib.parse import quote

from app.api.deps import get_request_context
from app.services.erpnext import ERPNextError, default_fields, request_erpnext

router = APIRouter(tags=["erpnext"])


@router.get("/picklists")
def get_picklists(
    filters: str | None = Query(default=None),
    context=Depends(get_request_context),
):
    params = {
        "fields": default_fields(["name", "status", "customer_name", "creation"]),
        "limit_page_length": 999,
    }
    if filters:
        params["filters"] = filters

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
    context=Depends(get_request_context),
):
    safe_name = quote(name, safe="")
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
    context=Depends(get_request_context),
):
    safe_name = quote(name, safe="")
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
    context=Depends(get_request_context),
):
    safe_code = quote(item_code, safe="")
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
    context=Depends(get_request_context),
):
    params = {
        "filters": filters,
        "fields": default_fields(["item_code", "custom_product_code"]),
    }
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
    context=Depends(get_request_context),
):
    params = {
        "fields": default_fields(["item_code", "item_name", "custom_product_code"]),
    }
    if limit_start is not None:
        params["limit_start"] = limit_start
    if limit_page_length is not None:
        params["limit_page_length"] = limit_page_length

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
    context=Depends(get_request_context),
):
    params = {
        "filters": filters,
        "fields": default_fields(
            [
                "actual_qty",
                "reserved_qty",
                "reserved_qty_for_production",
                "reserved_qty_for_sub_contract",
            ]
        ),
        "limit_page_length": 1,
    }
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
