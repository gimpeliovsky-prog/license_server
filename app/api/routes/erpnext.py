import json
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from urllib.parse import quote

from app.api.deps import get_db, get_erp_request_context, require_permissions
from app.schemas import (
    PickListCompleteRequest,
    PickListCompleteResponse,
    PickListFromSalesOrderCreateRequest,
    PickListFromSalesOrderCreateResponse,
    PickListFromSalesOrderPreviewResponse,
    PickListFromSalesOrderRequest,
    PickListFromSalesOrderShortageResponse,
    ProcessJobBatchRequest,
    ProcessJobResponse,
)
from app.services.audit import write_audit_log
from app.services.allowlist import Allowlist, get_allowlist, normalize_doctype, normalize_method
from app.services.erpnext import ERPNextError, default_fields, request_erpnext, request_tenant_erpnext
from app.services.idempotency import build_request_hash, extract_idempotency_key, get_replay_if_match, store_response
from app.models import ProcessJob, Tenant
from app.services.picklist_process import (
    PickListProcessError,
    create_delivery_note_from_pick_list,
    create_pick_list_from_preview,
    get_linked_delivery_note_name,
    get_linked_pick_list_name,
    ensure_document_submitted,
    preview_pick_list_from_sales_order,
    remember_delivery_note_link,
    remember_pick_list_link,
)
from app.services.process_jobs import (
    JOB_TYPE_PICKLIST_COMPLETE,
    create_process_job,
)
from app.services.process_job_runner import notify_process_job_runner
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


def serialize_process_job_response(job: ProcessJob) -> ProcessJobResponse:
    return ProcessJobResponse(
        job_id=str(job.id),
        job_type=job.job_type,
        status=job.status.value,
        correlation_id=job.correlation_id,
        result=job.result_meta,
        error_message=job.error_message,
        error_code=(job.result_meta or {}).get("error_code") if isinstance(job.result_meta, dict) else None,
        retryable=(job.result_meta or {}).get("retryable") if isinstance(job.result_meta, dict) else None,
    )


def process_error_detail(
    *,
    message: str,
    reason_code: str,
    retryable: bool = False,
    erp_refused: bool = True,
) -> dict[str, object]:
    return {
        "message": message,
        "reason_code": reason_code,
        "retryable": retryable,
        "erp_refused": erp_refused,
    }


def raise_process_http_error(exc: PickListProcessError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_detail()) from exc


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


def build_json_replay_response(replay) -> JSONResponse:
    return JSONResponse(
        content=json.loads(replay.body) if replay.body else None,
        status_code=replay.status_code,
        headers={"X-Idempotency-Replayed": "1"},
    )


def store_json_idempotent_response(
    db: Session,
    context,
    *,
    method: str,
    endpoint: str,
    idempotency_key: str | None,
    request_hash: str | None,
    payload,
    status_code: int = 200,
) -> JSONResponse:
    encoded = json.dumps(jsonable_encoder(payload), ensure_ascii=False)
    if idempotency_key and request_hash:
        concurrent_replay = store_response(
            db,
            context.tenant.id,
            method,
            endpoint,
            idempotency_key,
            request_hash,
            status_code,
            "application/json",
            encoded.encode("utf-8"),
        )
        if concurrent_replay:
            return build_json_replay_response(concurrent_replay)
    return JSONResponse(content=json.loads(encoded), status_code=status_code)


def ensure_picklist_process_supported(allowlist: Allowlist) -> None:
    ensure_method_allowed("GET", allowlist)
    ensure_method_allowed("POST", allowlist)
    get_allowed_doctype("Sales Order", allowlist)
    get_allowed_doctype("Pick List", allowlist)


def ensure_delivery_note_process_supported(allowlist: Allowlist) -> None:
    ensure_method_allowed("GET", allowlist)
    ensure_method_allowed("POST", allowlist)
    get_allowed_doctype("Pick List", allowlist)
    get_allowed_doctype("Delivery Note", allowlist)


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
        response = request_tenant_erpnext(
            context.tenant,
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
        response = request_tenant_erpnext(
            context.tenant,
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
        response = request_tenant_erpnext(
            context.tenant,
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
        response = request_tenant_erpnext(
            context.tenant,
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
        response = request_tenant_erpnext(
            context.tenant,
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


@router.post(
    "/process/picklists/from-sales-order/preview",
    response_model=PickListFromSalesOrderPreviewResponse,
)
def preview_picklist_from_sales_order_process(
    request: Request,
    payload: PickListFromSalesOrderRequest,
    db: Session = Depends(get_db),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_SALES_ORDERS_READ, PERMISSION_PICKLISTS_WRITE)
    ensure_picklist_process_supported(allowlist)
    idempotency_key = extract_idempotency_key(request)
    endpoint = "/process/picklists/from-sales-order/preview"
    request_payload = payload.model_dump(mode="json")
    request_hash = build_request_hash(request_payload) if idempotency_key else None
    if idempotency_key and request_hash:
        replay = get_replay_if_match(db, context.tenant.id, "POST", endpoint, idempotency_key, request_hash)
        if replay:
            return build_json_replay_response(replay)
    try:
        existing_pick_list_name = get_linked_pick_list_name(db, context.tenant, payload.sales_order_name)
        if existing_pick_list_name:
            response_payload = PickListFromSalesOrderPreviewResponse(
                sales_order_name=payload.sales_order_name.strip(),
                pick_list_name=existing_pick_list_name,
                existing_pick_list=True,
                allocated_line_count=0,
                shortage_count=0,
                can_create=False,
                shortages=[],
            )
            write_audit_log(
                db,
                context,
                action="picklist_preview_existing",
                meta={
                    "sales_order_name": payload.sales_order_name,
                    "pick_list_name": existing_pick_list_name,
                    "correlation_id": getattr(request.state, "correlation_id", None),
                },
            )
            db.commit()
            return store_json_idempotent_response(
                db,
                context,
                method="POST",
                endpoint=endpoint,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                payload=response_payload,
            )
        preview = preview_pick_list_from_sales_order(context.tenant, payload.sales_order_name)
    except PickListProcessError as exc:
        write_audit_log(
            db,
            context,
            action="picklist_preview_failed",
            meta={
                "sales_order_name": payload.sales_order_name,
                "detail": str(exc),
                "correlation_id": getattr(request.state, "correlation_id", None),
            },
        )
        db.commit()
        raise_process_http_error(exc)
    except ERPNextError as exc:
        write_audit_log(
            db,
            context,
            action="picklist_preview_failed",
            meta={
                "sales_order_name": payload.sales_order_name,
                "detail": str(exc),
                "correlation_id": getattr(request.state, "correlation_id", None),
            },
        )
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=process_error_detail(
                message=str(exc),
                reason_code="erp_unavailable",
                retryable=True,
                erp_refused=False,
            ),
        ) from exc

    response_payload = PickListFromSalesOrderPreviewResponse(
        sales_order_name=preview.sales_order_name,
        pick_list_name=None,
        existing_pick_list=False,
        allocated_line_count=preview.allocated_line_count,
        shortage_count=len(preview.shortages),
        can_create=preview.allocated_line_count > 0,
        shortages=[
            PickListFromSalesOrderShortageResponse(
                item_code=item.item_code,
                item_name=item.item_name,
                requested_qty=item.requested_qty,
                allocated_qty=item.allocated_qty,
                shortage_qty=item.shortage_qty,
            )
            for item in preview.shortages
        ],
    )
    write_audit_log(
        db,
        context,
        action="picklist_preview_succeeded",
        meta={
            "sales_order_name": payload.sales_order_name,
            "allocated_line_count": preview.allocated_line_count,
            "shortage_count": len(preview.shortages),
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )
    db.commit()
    return store_json_idempotent_response(
        db,
        context,
        method="POST",
        endpoint=endpoint,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        payload=response_payload,
    )


@router.post(
    "/process/picklists/from-sales-order",
    response_model=PickListFromSalesOrderCreateResponse,
)
def create_picklist_from_sales_order_process(
    request: Request,
    payload: PickListFromSalesOrderCreateRequest,
    db: Session = Depends(get_db),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_SALES_ORDERS_READ, PERMISSION_PICKLISTS_WRITE)
    ensure_picklist_process_supported(allowlist)
    idempotency_key = extract_idempotency_key(request)
    endpoint = "/process/picklists/from-sales-order"
    request_payload = payload.model_dump(mode="json")
    request_hash = build_request_hash(request_payload) if idempotency_key else None
    if idempotency_key and request_hash:
        replay = get_replay_if_match(db, context.tenant.id, "POST", endpoint, idempotency_key, request_hash)
        if replay:
            return build_json_replay_response(replay)
    (
        db.query(Tenant)
        .filter(Tenant.id == context.tenant.id)
        .with_for_update()
        .first()
    )
    try:
        existing_pick_list_name = get_linked_pick_list_name(db, context.tenant, payload.sales_order_name)
        if existing_pick_list_name:
            response_payload = PickListFromSalesOrderCreateResponse(
                sales_order_name=payload.sales_order_name.strip(),
                pick_list_name=existing_pick_list_name,
                created=False,
                allocated_line_count=0,
                shortage_count=0,
                has_shortages=False,
            )
            write_audit_log(
                db,
                context,
                action="picklist_create_existing",
                meta={
                    "sales_order_name": payload.sales_order_name,
                    "pick_list_name": existing_pick_list_name,
                    "correlation_id": getattr(request.state, "correlation_id", None),
                },
            )
            db.commit()
            return store_json_idempotent_response(
                db,
                context,
                method="POST",
                endpoint=endpoint,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                payload=response_payload,
            )
        ensure_document_submitted(context.tenant, "Sales Order", payload.sales_order_name)
        preview = preview_pick_list_from_sales_order(context.tenant, payload.sales_order_name)
        if preview.allocated_line_count <= 0:
            raise HTTPException(
                status_code=409,
                detail=process_error_detail(
                    message="ERPNext did not allocate stock for this Sales Order",
                    reason_code="no_stock_allocated",
                    retryable=False,
                    erp_refused=True,
                ),
            )
        if preview.shortages and not payload.allow_partial:
            raise HTTPException(
                status_code=409,
                detail=process_error_detail(
                    message="Shortages detected for this Sales Order",
                    reason_code="stock_shortage",
                    retryable=False,
                    erp_refused=True,
                ),
            )
        pick_list_name = create_pick_list_from_preview(context.tenant, preview)
        remember_pick_list_link(db, context.tenant, payload.sales_order_name, pick_list_name)
    except PickListProcessError as exc:
        write_audit_log(
            db,
            context,
            action="picklist_create_failed",
            meta={
                "sales_order_name": payload.sales_order_name,
                "detail": str(exc),
                "correlation_id": getattr(request.state, "correlation_id", None),
            },
        )
        db.commit()
        raise_process_http_error(exc)
    except ERPNextError as exc:
        write_audit_log(
            db,
            context,
            action="picklist_create_failed",
            meta={
                "sales_order_name": payload.sales_order_name,
                "detail": str(exc),
                "correlation_id": getattr(request.state, "correlation_id", None),
            },
        )
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=process_error_detail(
                message=str(exc),
                reason_code="erp_unavailable",
                retryable=True,
                erp_refused=False,
            ),
        ) from exc

    response_payload = PickListFromSalesOrderCreateResponse(
        sales_order_name=preview.sales_order_name,
        pick_list_name=pick_list_name,
        created=True,
        allocated_line_count=preview.allocated_line_count,
        shortage_count=len(preview.shortages),
        has_shortages=bool(preview.shortages),
    )
    write_audit_log(
        db,
        context,
        action="picklist_create_succeeded",
        meta={
            "sales_order_name": payload.sales_order_name,
            "pick_list_name": pick_list_name,
            "allocated_line_count": preview.allocated_line_count,
            "shortage_count": len(preview.shortages),
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )
    db.commit()
    return store_json_idempotent_response(
        db,
        context,
        method="POST",
        endpoint=endpoint,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        payload=response_payload,
    )


@router.post(
    "/process/picklists/{pick_list_name}/complete",
    response_model=PickListCompleteResponse,
)
def complete_picklist_process(
    request: Request,
    pick_list_name: str,
    payload: PickListCompleteRequest,
    db: Session = Depends(get_db),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_PICKLISTS_WRITE)
    if payload.create_delivery_note:
        ensure_delivery_note_process_supported(allowlist)
    idempotency_key = extract_idempotency_key(request)
    endpoint = f"/process/picklists/{pick_list_name}/complete"
    request_payload = payload.model_dump(mode="json")
    request_hash = build_request_hash(request_payload) if idempotency_key else None
    if idempotency_key and request_hash:
        replay = get_replay_if_match(db, context.tenant.id, "POST", endpoint, idempotency_key, request_hash)
        if replay:
            return build_json_replay_response(replay)
    try:
        ensure_document_submitted(context.tenant, "Pick List", pick_list_name)
        delivery_note_name = None
        if payload.create_delivery_note:
            linked_delivery_note_name = get_linked_delivery_note_name(db, context.tenant, pick_list_name)
            if linked_delivery_note_name:
                delivery_note_name = linked_delivery_note_name
            else:
                completion_lines = payload.model_dump(mode="json").get("lines", [])
                delivery_note_name = create_delivery_note_from_pick_list(
                    context.tenant,
                    pick_list_name,
                    completion_lines=completion_lines,
                )
                remember_delivery_note_link(db, context.tenant, pick_list_name, delivery_note_name)
    except PickListProcessError as exc:
        write_audit_log(
            db,
            context,
            action="picklist_complete_failed",
            meta={
                "pick_list_name": pick_list_name,
                "detail": str(exc),
                "correlation_id": getattr(request.state, "correlation_id", None),
            },
        )
        db.commit()
        raise_process_http_error(exc)
    except ERPNextError as exc:
        write_audit_log(
            db,
            context,
            action="picklist_complete_failed",
            meta={
                "pick_list_name": pick_list_name,
                "detail": str(exc),
                "correlation_id": getattr(request.state, "correlation_id", None),
            },
        )
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=process_error_detail(
                message=str(exc),
                reason_code="erp_unavailable",
                retryable=True,
                erp_refused=False,
            ),
        ) from exc

    response_payload = PickListCompleteResponse(
        pick_list_name=pick_list_name,
        delivery_note_created=delivery_note_name is not None,
        delivery_note_name=delivery_note_name,
    )
    write_audit_log(
        db,
        context,
        action="picklist_complete_succeeded",
        meta={
            "pick_list_name": pick_list_name,
            "delivery_note_name": delivery_note_name,
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )
    db.commit()
    return store_json_idempotent_response(
        db,
        context,
        method="POST",
        endpoint=endpoint,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        payload=response_payload,
    )


@router.post(
    "/process/picklists/{pick_list_name}/complete-async",
    response_model=ProcessJobResponse,
)
def complete_picklist_process_async(
    request: Request,
    pick_list_name: str,
    payload: PickListCompleteRequest,
    db: Session = Depends(get_db),
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_PICKLISTS_WRITE)
    if payload.create_delivery_note:
        ensure_delivery_note_process_supported(allowlist)
    idempotency_key = extract_idempotency_key(request)
    endpoint = f"/process/picklists/{pick_list_name}/complete-async"
    request_payload = {"pick_list_name": pick_list_name, **payload.model_dump(mode="json")}
    request_hash = build_request_hash(request_payload) if idempotency_key else None
    if idempotency_key and request_hash:
        replay = get_replay_if_match(db, context.tenant.id, "POST", endpoint, idempotency_key, request_hash)
        if replay:
            return build_json_replay_response(replay)

    request_key = None
    if idempotency_key:
        request_key = f"{context.tenant.id}:POST:{endpoint}:{idempotency_key}"
    job = create_process_job(
        db,
        tenant_id=context.tenant.id,
        device_id=context.device.id if context.device else None,
        job_type=JOB_TYPE_PICKLIST_COMPLETE,
        correlation_id=getattr(request.state, "correlation_id", None),
        request_key=request_key,
        request_meta={
            "pick_list_name": pick_list_name,
            "create_delivery_note": payload.create_delivery_note,
            "lines": payload.model_dump(mode="json").get("lines", []),
        },
    )
    notify_process_job_runner()
    write_audit_log(
        db,
        context,
        action="picklist_complete_async_queued",
        meta={
            "pick_list_name": pick_list_name,
            "job_id": str(job.id),
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )
    db.commit()
    response_payload = ProcessJobResponse(
        job_id=str(job.id),
        job_type=job.job_type,
        status=job.status.value,
        correlation_id=job.correlation_id,
        result=job.result_meta,
        error_message=job.error_message,
        error_code=(job.result_meta or {}).get("error_code") if isinstance(job.result_meta, dict) else None,
        retryable=(job.result_meta or {}).get("retryable") if isinstance(job.result_meta, dict) else None,
    )
    return store_json_idempotent_response(
        db,
        context,
        method="POST",
        endpoint=endpoint,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        payload=response_payload,
        status_code=202,
    )


@router.get(
    "/process/jobs/{job_id}",
    response_model=ProcessJobResponse,
)
def get_process_job(
    job_id: str,
    db: Session = Depends(get_db),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_PICKLISTS_WRITE)
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid process job id") from exc
    job = (
        db.query(ProcessJob)
        .filter(
            ProcessJob.id == job_uuid,
            ProcessJob.tenant_id == context.tenant.id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Process job not found")
    return serialize_process_job_response(job)


@router.post(
    "/process/jobs/batch",
    response_model=list[ProcessJobResponse],
)
def get_process_jobs_batch(
    payload: ProcessJobBatchRequest,
    db: Session = Depends(get_db),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_PICKLISTS_WRITE)
    normalized_ids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for raw_id in payload.job_ids:
        text = raw_id.strip()
        if not text:
            continue
        try:
            job_uuid = uuid.UUID(text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid process job id: {text}") from exc
        if job_uuid in seen:
            continue
        seen.add(job_uuid)
        normalized_ids.append(job_uuid)

    if not normalized_ids:
        return []

    jobs = (
        db.query(ProcessJob)
        .filter(
            ProcessJob.id.in_(normalized_ids),
            ProcessJob.tenant_id == context.tenant.id,
        )
        .all()
    )
    jobs_by_id = {job.id: job for job in jobs}
    missing = [str(job_id) for job_id in normalized_ids if job_id not in jobs_by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"Process job not found: {missing[0]}")

    return [serialize_process_job_response(jobs_by_id[job_id]) for job_id in normalized_ids]


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
        response = request_tenant_erpnext(
            context.tenant,
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
        response = request_tenant_erpnext(
            context.tenant,
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
        response = request_tenant_erpnext(
            context.tenant,
            "POST",
            "/api/resource/Sales Order",
            json_body=payload,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    response_content = response.content
    response_status = response.status_code
    response_content_type = response.headers.get("content-type")

    if idempotency_key and request_hash:
        concurrent_replay = store_response(
            db,
            context.tenant.id,
            "POST",
            endpoint,
            idempotency_key,
            request_hash,
            response_status,
            response_content_type,
            response_content,
        )
        if concurrent_replay:
            return build_proxy_response(
                concurrent_replay.body,
                concurrent_replay.status_code,
                concurrent_replay.content_type,
                replayed=True,
            )

    return build_proxy_response(response_content, response_status, response_content_type)


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
        response = request_tenant_erpnext(
            context.tenant,
            "PUT",
            f"/api/resource/Sales Order/{safe_name}",
            json_body=payload,
        )
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    response_content = response.content
    response_status = response.status_code
    response_content_type = response.headers.get("content-type")

    if idempotency_key and request_hash:
        concurrent_replay = store_response(
            db,
            context.tenant.id,
            "PUT",
            endpoint,
            idempotency_key,
            request_hash,
            response_status,
            response_content_type,
            response_content,
        )
        if concurrent_replay:
            return build_proxy_response(
                concurrent_replay.body,
                concurrent_replay.status_code,
                concurrent_replay.content_type,
                replayed=True,
            )

    return build_proxy_response(response_content, response_status, response_content_type)


@router.post("/sales-orders/{name}/submit")
def submit_sales_order(
    name: str,
    allowlist: Allowlist = Depends(get_allowlist_dep),
    context=Depends(get_erp_request_context),
):
    require_permissions(context, PERMISSION_SALES_ORDERS_WRITE)
    ensure_method_allowed("POST", allowlist)
    get_allowed_doctype("Sales Order", allowlist)

    try:
        submitted = ensure_document_submitted(context.tenant, "Sales Order", name)
    except PickListProcessError as exc:
        raise_process_http_error(exc)

    return JSONResponse(content={"data": submitted}, status_code=200)


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
        response = request_tenant_erpnext(
            context.tenant,
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
