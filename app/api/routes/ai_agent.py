"""API for AI Sales Manager."""

from __future__ import annotations

import json
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_ai_agent
from app.models import AIConversation, AIConversationMessage, AuditLog, BuyerChannelIdentity
from app.models import LicenseKey, LicenseKeyStatus, Tenant, TenantStatus
from app.models.tenant_channel import TenantChannel
from app.services.ai_handoff import dispatch_handoff
from app.services.erp_catalog import get_item_detail, list_items
from app.services.erpnext import (
    ERPNextError,
    request_tenant_erpnext,
)
from app.services.erp_sales import (
    build_sales_order_summary,
    create_invoice_from_sales_order,
    create_sales_order as create_sales_order_for_tenant,
    fetch_sales_order_doc,
    update_sales_order_items as update_sales_order_items_for_tenant,
)
from app.services.license import fingerprint_license_key, hash_license_key
from app.services.subscription import evaluate_subscription
from app.utils.time import utcnow

router = APIRouter(prefix="/ai-agent", tags=["ai-agent"], dependencies=[Depends(require_ai_agent)])


class TenantContextResponse(BaseModel):
    tenant_id: str
    company_code: str
    company_name: str | None
    erpnext_url: str
    api_key: str
    api_secret: str
    subscription_active: bool
    ai_system_prompt: str | None
    ai_language: str
    ai_policy: dict[str, Any]


class ChannelLookupResponse(BaseModel):
    found: bool
    tenant: TenantContextResponse | None = None


class BuyerLookupResponse(BaseModel):
    found: bool
    erp_customer_name: str | None = None
    erp_customer_id: str | None = None
    buyer_identity_id: str | None = None
    phone: str | None = None
    channel: str | None = None
    recognized_via: str | None = None
    is_returning_customer: bool = False
    recent_sales_orders: list[dict[str, Any]] = Field(default_factory=list)
    recent_sales_invoices: list[dict[str, Any]] = Field(default_factory=list)


class CreateBuyerRequest(BaseModel):
    company_code: str
    full_name: str
    phone: str | None = None
    tg_chat_id: str | None = None
    channel_type: str | None = None
    channel_user_id: str | None = None
    email: str | None = None


class ResolveBuyerRequest(BaseModel):
    channel_type: str
    channel_user_id: str
    phone: str | None = None
    full_name: str | None = None


class CreateLicenseRequest(BaseModel):
    company_code: str
    description: str | None = None


class ExtendSubscriptionRequest(BaseModel):
    add_days: int


class CreateSalesOrderRequest(BaseModel):
    company_code: str
    customer: str
    delivery_date: str | None = None
    items: list[dict]


class CreateInvoiceRequest(BaseModel):
    company_code: str
    sales_order_name: str


class UpdateSalesOrderRequest(BaseModel):
    company_code: str
    sales_order_name: str
    items: list[dict]


class AIConversationEventRequest(BaseModel):
    event_type: str
    session_id: str | None = None
    channel_type: str | None = None
    channel_user_id: str | None = None
    buyer_identity_id: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)


class AIHandoffRequest(BaseModel):
    channel_type: str
    channel_user_id: str
    session_id: str | None = None
    buyer_identity_id: str | None = None
    reason: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)


class AITranscriptMessageRequest(BaseModel):
    message_id: str
    channel_type: str
    channel_user_id: str
    role: str
    message_type: str = "chat"
    content: str | None = None
    stage: str | None = None
    behavior_class: str | None = None
    tool_name: str | None = None
    buyer_identity_id: str | None = None
    erp_customer_id: str | None = None
    buyer_name: str | None = None
    buyer_phone: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)


def _safe_text(value: Any, *, limit: int = 255) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:limit]


def _get_tenant(db: Session, company_code: str) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.company_code == company_code).first()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{company_code}' not found")
    return tenant


def _subscription_active(tenant: Tenant) -> bool:
    if tenant.is_system:
        return True
    state = evaluate_subscription(subscription_expires_at=tenant.subscription_expires_at, issued_at=tenant.created_at)
    return state.allowed


def _erp(tenant: Tenant, method: str, path: str, **kwargs) -> Any:
    try:
        return request_tenant_erpnext(tenant, method, path, **kwargs)
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _safe_json_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def _normalize_phone(value: str | None) -> str | None:
    digits = "".join(ch for ch in str(value or "").strip() if ch.isdigit() or ch == "+")
    if not digits:
        return None
    if digits.startswith("+"):
        normalized = "+" + "".join(ch for ch in digits[1:] if ch.isdigit())
    else:
        normalized = "+" + "".join(ch for ch in digits if ch.isdigit())
    return normalized if len("".join(ch for ch in normalized if ch.isdigit())) >= 8 else None


def _load_sales_history(tenant: Tenant, erp_customer_id: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not erp_customer_id:
        return [], []

    sales_orders: list[dict[str, Any]] = []
    sales_invoices: list[dict[str, Any]] = []

    sales_order_resp = _erp(
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

    sales_invoice_resp = _erp(
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


def _buyer_lookup_response(
    *,
    tenant: Tenant,
    erp_customer_id: str,
    erp_customer_name: str | None,
    identity: BuyerChannelIdentity | None = None,
    phone: str | None = None,
    channel: str | None = None,
    recognized_via: str | None = None,
) -> BuyerLookupResponse:
    sales_orders, sales_invoices = _load_sales_history(tenant, erp_customer_id)
    return BuyerLookupResponse(
        found=True,
        erp_customer_name=erp_customer_name,
        erp_customer_id=erp_customer_id,
        buyer_identity_id=str(identity.id) if identity else None,
        phone=_normalize_phone(phone or (identity.phone if identity else None)),
        channel=channel or (identity.channel if identity else None),
        recognized_via=recognized_via,
        is_returning_customer=bool(sales_orders or sales_invoices),
        recent_sales_orders=sales_orders,
        recent_sales_invoices=sales_invoices,
    )


def _resolve_erp_customer_by_phone(tenant: Tenant, phone: str | None) -> tuple[str | None, str | None]:
    normalized_phone = _normalize_phone(phone)
    if not normalized_phone:
        return None, None
    resp = _erp(
        tenant,
        "GET",
        "/api/resource/Contact",
        params={
            "filters": json.dumps([["mobile_no", "=", normalized_phone]]),
            "fields": json.dumps(["name", "full_name", "mobile_no"]),
            "limit_page_length": 5,
        },
    )
    if resp.status_code != 200:
        return None, None
    contacts = resp.json().get("data", [])
    if not contacts:
        return None, None
    contact_name = contacts[0]["name"]
    resp2 = _erp(
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
    if resp2.status_code != 200 or not resp2.json().get("data"):
        return None, None
    customer_name = resp2.json()["data"][0]["link_name"]
    return customer_name, contacts[0].get("full_name")


def _upsert_buyer_identity(
    *,
    db: Session,
    tenant: Tenant,
    channel: str,
    channel_user_id: str,
    erp_customer_id: str,
    phone: str | None = None,
    full_name: str | None = None,
) -> BuyerChannelIdentity:
    identity = (
        db.query(BuyerChannelIdentity)
        .filter(
            BuyerChannelIdentity.tenant_id == tenant.id,
            BuyerChannelIdentity.channel == channel,
            BuyerChannelIdentity.channel_user_id == channel_user_id,
        )
        .first()
    )
    if not identity:
        identity = BuyerChannelIdentity(
            tenant_id=tenant.id,
            channel=channel,
            channel_user_id=channel_user_id,
            erp_customer_id=erp_customer_id,
        )
        db.add(identity)
    identity.erp_customer_id = erp_customer_id
    normalized_phone = _normalize_phone(phone)
    if normalized_phone:
        identity.phone = normalized_phone
    if full_name:
        identity.full_name = full_name
    return identity


def _channel_ai_policy(channel: TenantChannel | None, tenant: Tenant) -> dict[str, Any]:
    tenant_config = _safe_json_dict(tenant.tenant_config)
    ai_config = _safe_json_dict(tenant_config.get("ai"))
    handoff_rules = _safe_json_dict(ai_config.get("handoff_rules"))
    handoff_target = _safe_json_dict(ai_config.get("handoff_target"))
    tools_policy = _safe_json_dict(ai_config.get("tools_policy"))
    classification = _safe_json_dict(ai_config.get("classification"))
    prompt_overrides = _safe_json_dict(ai_config.get("prompt_overrides"))
    default_allowed_tools = [
        "get_product_catalog",
        "register_buyer",
        "get_buyer_sales_history",
        "create_sales_order",
        "update_sales_order",
        "send_sales_order_pdf",
        "create_invoice",
        "create_license",
        "extend_subscription",
    ]

    allow_invoice = bool(ai_config.get("allow_invoice", True))
    allow_license_ops = bool(ai_config.get("allow_license_ops", True))
    allow_discount_promises = bool(ai_config.get("allow_discount_promises", False))
    allow_free_text_catalog_answers = bool(ai_config.get("allow_free_text_catalog_answers", True))
    configured_tools = tools_policy.get("allowed_tools")
    if isinstance(configured_tools, list) and configured_tools:
        allowed_tools = [str(item).strip() for item in configured_tools if str(item).strip()]
    else:
        allowed_tools = list(default_allowed_tools)
    if not allow_invoice:
        allowed_tools = [tool for tool in allowed_tools if tool != "create_invoice"]
    if not allow_license_ops:
        allowed_tools = [tool for tool in allowed_tools if tool not in {"create_license", "extend_subscription"}]

    return {
        "allow_invoice": allow_invoice,
        "allow_license_ops": allow_license_ops,
        "allow_discount_promises": allow_discount_promises,
        "allow_free_text_catalog_answers": allow_free_text_catalog_answers,
        "allowed_tools": allowed_tools,
        "handoff_rules": {
            "enabled": bool(handoff_rules.get("enabled", True)),
            "clarification_failure_limit": int(handoff_rules.get("clarification_failure_limit", 2) or 2),
            "allow_customer_requested_handoff": bool(handoff_rules.get("allow_customer_requested_handoff", True)),
            "frustrated_customer_handoff": bool(handoff_rules.get("frustrated_customer_handoff", True)),
        },
        "handoff_target": {
            "target_type": str(handoff_target.get("target_type") or "none"),
            "destination": handoff_target.get("destination"),
            "instructions": handoff_target.get("instructions"),
        },
        "tools_policy": tools_policy,
        "classification": classification,
        "prompt_overrides": prompt_overrides,
        "channel": {
            "webchat_allowed_origins": _safe_json_list(channel.webchat_allowed_origins if channel else None),
            "webchat_widget_token": channel.webchat_widget_token if channel else None,
        },
    }


def _build_tenant_context(tenant: Tenant, channel: TenantChannel | None) -> TenantContextResponse:
    return TenantContextResponse(
        tenant_id=str(tenant.id),
        company_code=tenant.company_code,
        company_name=tenant.company_name,
        erpnext_url=tenant.erpnext_url,
        api_key=tenant.api_key,
        api_secret=tenant.api_secret,
        subscription_active=_subscription_active(tenant),
        ai_system_prompt=channel.ai_system_prompt if channel else None,
        ai_language=(channel.ai_language if channel else None) or "ru",
        ai_policy=_channel_ai_policy(channel, tenant),
    )


def _audit_ai_event(db: Session, tenant: Tenant, action: str, meta: dict[str, Any]) -> AuditLog:
    entry = AuditLog(
        tenant_id=tenant.id,
        action=action,
        meta=meta,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def _conversation_identity_uuid(value: str | None) -> Any:
    if not value:
        return None
    try:
        from uuid import UUID

        return UUID(str(value))
    except Exception:
        return None


def _get_or_create_conversation(
    *,
    db: Session,
    tenant: Tenant,
    session_id: str,
    channel_type: str,
    channel_user_id: str,
    buyer_identity_id: str | None = None,
    erp_customer_id: str | None = None,
    buyer_name: str | None = None,
    buyer_phone: str | None = None,
    stage: str | None = None,
) -> AIConversation:
    conversation = (
        db.query(AIConversation)
        .filter(AIConversation.tenant_id == tenant.id, AIConversation.session_id == session_id)
        .first()
    )
    if not conversation:
        conversation = AIConversation(
            tenant_id=tenant.id,
            session_id=session_id,
            channel_type=channel_type,
            channel_user_id=channel_user_id,
        )
        db.add(conversation)
        db.flush()
    conversation.channel_type = channel_type
    conversation.channel_user_id = channel_user_id
    if buyer_identity_id:
        conversation.buyer_identity_id = _conversation_identity_uuid(buyer_identity_id)
    if erp_customer_id:
        conversation.erp_customer_id = erp_customer_id
    if buyer_name:
        conversation.buyer_name = buyer_name
    normalized_phone = _normalize_phone(buyer_phone)
    if normalized_phone:
        conversation.buyer_phone = normalized_phone
    if stage:
        conversation.last_stage = stage
    return conversation


def _quality_flags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _conversation_status_from_runtime(
    *,
    current_status: str | None,
    message_type: str | None = None,
    stage: str | None = None,
    lead_status: str | None = None,
    handoff_reason: str | None = None,
    sales_owner_status: str | None = None,
) -> str:
    if message_type == "closed" or lead_status in {"won", "lost", "merged"}:
        return "closed"
    if message_type == "handoff" or stage == "handoff" or handoff_reason or lead_status == "handoff":
        return "handoff"
    if sales_owner_status in {"accepted", "delivered", "reassigned_requested"}:
        return "handoff"
    return current_status or "open"


def _build_conversation_summary(
    *,
    conversation: AIConversation,
    payload: dict[str, Any] | None,
    content: str | None = None,
) -> str | None:
    payload = payload if isinstance(payload, dict) else {}
    lead_profile = payload.get("lead_profile") if isinstance(payload.get("lead_profile"), dict) else {}
    parts: list[str] = []
    lead_status = _safe_text(lead_profile.get("status"), limit=32)
    product_interest = _safe_text(lead_profile.get("product_interest"), limit=120)
    next_action = _safe_text(lead_profile.get("next_action"), limit=64)
    handoff_reason = _safe_text(payload.get("handoff_reason") or payload.get("reason") or conversation.handoff_reason, limit=64)
    quality_flags = _quality_flags(payload.get("quality_flags"))
    if lead_status:
        parts.append(lead_status)
    if product_interest:
        parts.append(product_interest)
    if next_action:
        parts.append(f"next: {next_action}")
    if handoff_reason:
        parts.append(f"handoff: {handoff_reason}")
    if quality_flags:
        parts.append(f"quality: {', '.join(quality_flags[:2])}")
    preview = _safe_text(content, limit=160)
    if preview and not parts:
        parts.append(preview)
    return " | ".join(parts)[:500] if parts else conversation.summary


def _sync_conversation_runtime_state(
    *,
    conversation: AIConversation,
    payload: dict[str, Any] | None,
    stage: str | None = None,
    message_type: str | None = None,
    content: str | None = None,
    event_type: str | None = None,
    delivery: dict[str, Any] | None = None,
) -> None:
    payload = payload if isinstance(payload, dict) else {}
    lead_profile = payload.get("lead_profile") if isinstance(payload.get("lead_profile"), dict) else {}
    if stage:
        conversation.last_stage = stage
    elif payload.get("stage"):
        conversation.last_stage = _safe_text(payload.get("stage"), limit=64)

    if lead_profile:
        conversation.lead_id = _safe_text(lead_profile.get("lead_id"), limit=64) or conversation.lead_id
        conversation.lead_status = _safe_text(lead_profile.get("status"), limit=32) or conversation.lead_status
        conversation.lead_temperature = _safe_text(lead_profile.get("temperature"), limit=16) or conversation.lead_temperature
        conversation.next_action = _safe_text(lead_profile.get("next_action"), limit=64) or conversation.next_action
        conversation.sales_owner_status = _safe_text(lead_profile.get("sales_owner_status"), limit=64) or conversation.sales_owner_status
        conversation.handoff_reason = _safe_text(payload.get("handoff_reason") or payload.get("reason") or conversation.handoff_reason, limit=64)
    elif payload.get("reason"):
        conversation.handoff_reason = _safe_text(payload.get("reason"), limit=64) or conversation.handoff_reason

    quality_score = payload.get("quality_score", payload.get("conversation_quality_score"))
    if isinstance(quality_score, (int, float)):
        conversation.quality_score = int(quality_score)
    quality_flags = _quality_flags(payload.get("quality_flags"))
    if quality_flags:
        conversation.quality_flags_json = {"flags": quality_flags}

    if event_type:
        conversation.last_event_type = _safe_text(event_type, limit=64)
        conversation.last_event_at = utcnow()
    if delivery:
        delivery_status = _safe_text(delivery.get("status"), limit=32)
        if delivery_status:
            conversation.last_delivery_status = delivery_status

    conversation.status = _conversation_status_from_runtime(
        current_status=conversation.status,
        message_type=message_type,
        stage=conversation.last_stage,
        lead_status=conversation.lead_status,
        handoff_reason=conversation.handoff_reason,
        sales_owner_status=conversation.sales_owner_status,
    )
    conversation.summary = _build_conversation_summary(
        conversation=conversation,
        payload=payload,
        content=content,
    )


def _desk_form_url(base_url: str, doctype_route: str, docname: str | None) -> str | None:
    if not docname:
        return None
    base = (base_url or "").rstrip("/")
    if not base:
        return None
    return f"{base}/app/{doctype_route}/{quote(docname, safe='')}"


def _printview_url(base_url: str, doctype: str, docname: str | None, format_name: str = "Standard") -> str | None:
    if not docname:
        return None
    base = (base_url or "").rstrip("/")
    if not base:
        return None
    return (
        f"{base}/api/method/frappe.utils.print_format.download_pdf?"
        f"doctype={quote(doctype, safe='')}&name={quote(docname, safe='')}"
        f"&format={quote(format_name, safe='')}&no_letterhead=0"
    )


def _absolute_media_url(base_url: str, value: str | None) -> str | None:
    path = str(value or "").strip()
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    return f"{(base_url or '').rstrip('/')}/{path.lstrip('/')}" if base_url else None


def _fetch_item_doc(tenant: Tenant, item_code: str) -> dict[str, Any] | None:
    response = _erp(tenant, "GET", f"/api/resource/Item/{quote(item_code, safe='')}")
    if response.status_code != 200:
        return None
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else None


def _resolve_item_doc(tenant: Tenant, item_ref: str) -> dict[str, Any] | None:
    item_ref = str(item_ref or "").strip()
    if not item_ref:
        return None

    item_doc = _fetch_item_doc(tenant, item_ref)
    if item_doc:
        return item_doc

    search_variants = [
        [["disabled", "=", 0], ["item_name", "=", item_ref]],
        [["disabled", "=", 0], ["item_name", "like", f"%{item_ref}%"]],
    ]
    for filters in search_variants:
        resp = _erp(
            tenant,
            "GET",
            "/api/resource/Item",
            params={
                "fields": json.dumps(["item_code", "item_name", "item_group", "description", "stock_uom"]),
                "filters": json.dumps(filters),
                "limit_page_length": 1,
            },
        )
        if resp.status_code != 200:
            continue
        data = resp.json().get("data", [])
        if not data:
            continue
        resolved_item_code = data[0].get("item_code")
        if resolved_item_code:
            item_doc = _fetch_item_doc(tenant, resolved_item_code)
            if item_doc:
                return item_doc
    return None


def _fetch_item_translation(tenant: Tenant, source_text: str | None, lang: str | None) -> str | None:
    if not source_text or not lang or lang == "en":
        return None
    resp = _erp(
        tenant,
        "GET",
        "/api/resource/Translation",
        params={
            "fields": json.dumps(["source_text", "translated_text", "language"]),
            "filters": json.dumps([["source_text", "=", source_text], ["language", "=", lang]]),
            "limit_page_length": 1,
        },
    )
    if resp.status_code != 200:
        return None
    data = resp.json().get("data", [])
    if not data:
        return None
    translated = str(data[0].get("translated_text") or "").strip()
    return translated or None


def _fetch_item_price(tenant: Tenant, item_code: str | None) -> tuple[float | None, str | None]:
    if not item_code:
        return None, None
    resp = _erp(
        tenant,
        "GET",
        "/api/resource/Item Price",
        params={
            "fields": json.dumps(["item_code", "price_list_rate", "currency", "price_list", "selling"]),
            "filters": json.dumps([["item_code", "=", item_code], ["selling", "=", 1]]),
            "order_by": "modified desc",
            "limit_page_length": 1,
        },
    )
    if resp.status_code != 200:
        return None, None
    data = resp.json().get("data", [])
    if not data:
        return None, None
    row = data[0]
    return row.get("price_list_rate"), row.get("currency")


def _extract_item_uoms(item_doc: dict[str, Any], fallback_stock_uom: str | None = None) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    stock_uom = item_doc.get("stock_uom") or fallback_stock_uom
    sales_uom = item_doc.get("sales_uom")
    available_uoms: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add_uom(raw_uom: Any, raw_conversion: Any) -> None:
        if not isinstance(raw_uom, str):
            return
        uom = raw_uom.strip()
        if not uom:
            return
        key = uom.lower()
        if key in seen:
            return
        conversion_factor: float | None = None
        if isinstance(raw_conversion, (int, float)):
            conversion_factor = float(raw_conversion)
            if conversion_factor <= 0:
                conversion_factor = 1.0
        elif stock_uom and uom.lower() == str(stock_uom).lower():
            conversion_factor = 1.0
        available_uoms.append(
            {
                "uom": uom,
                "conversion_factor": conversion_factor,
                "is_stock_uom": bool(stock_uom and uom.lower() == str(stock_uom).lower()),
            }
        )
        seen.add(key)

    _add_uom(stock_uom, 1.0)
    for row in item_doc.get("uoms", []):
        if isinstance(row, dict):
            _add_uom(row.get("uom"), row.get("conversion_factor"))
    _add_uom(sales_uom, 1.0 if sales_uom and stock_uom and str(sales_uom).lower() == str(stock_uom).lower() else None)

    return stock_uom, sales_uom, available_uoms


def _humanize_uom(uom: str | None, lang: str = "ru") -> str | None:
    if not uom:
        return None
    normalized = uom.strip().lower()
    labels_ru = {
        "nos": "штуки",
        "unit": "штуки",
        "pcs": "штуки",
        "pc": "штуки",
        "piece": "штуки",
        "box": "коробки",
        "boxes": "коробки",
        "pack": "упаковки",
        "packet": "упаковки",
        "kg": "килограммы",
        "g": "граммы",
        "l": "литры",
        "m": "метры",
    }
    labels_en = {
        "nos": "pieces",
        "unit": "pieces",
        "pcs": "pieces",
        "pc": "pieces",
        "piece": "pieces",
        "box": "boxes",
        "boxes": "boxes",
        "pack": "packs",
        "packet": "packs",
        "kg": "kilograms",
        "g": "grams",
        "l": "liters",
        "m": "meters",
    }
    labels = labels_ru if lang == "ru" else labels_en
    return labels.get(normalized, uom)


def _customer_uom_summary(stock_uom: str | None, non_stock_uoms: list[dict[str, Any]], lang: str = "ru") -> str | None:
    stock_label = _humanize_uom(stock_uom, lang)
    if not non_stock_uoms:
        if not stock_label:
            return None
        if lang == "ru":
            return f"Товар продается в единицах: {stock_label}."
        return f"This product is sold in: {stock_label}."

    non_stock_labels = [str(uom.get("display_name") or uom.get("uom")) for uom in non_stock_uoms if uom.get("display_name") or uom.get("uom")]
    if not non_stock_labels:
        if not stock_label:
            return None
        if lang == "ru":
            return f"Товар продается в единицах: {stock_label}."
        return f"This product is sold in: {stock_label}."

    all_labels: list[str] = []
    if stock_label:
        all_labels.append(stock_label)
    all_labels.extend([label for label in non_stock_labels if label not in all_labels])
    if lang == "ru":
        return f"Товар продается в единицах: {', '.join(all_labels)}."
    return f"This product is sold in: {', '.join(all_labels)}."


def _normalize_sales_order_item(item: dict[str, Any]) -> dict[str, Any] | None:
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


def _build_new_sales_order_item(item: dict[str, Any]) -> dict[str, Any] | None:
    normalized = _normalize_sales_order_item(item)
    if not normalized:
        return None
    normalized["doctype"] = "Sales Order Item"
    normalized["parenttype"] = "Sales Order"
    normalized["parentfield"] = "items"
    return normalized


def _sanitize_existing_sales_order_item(item: dict[str, Any]) -> dict[str, Any]:
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


@router.get("/resolve/telegram/{bot_token}", response_model=ChannelLookupResponse)
def resolve_by_telegram_token(bot_token: str, db: Session = Depends(get_db)) -> ChannelLookupResponse:
    channel = db.query(TenantChannel).filter(TenantChannel.tg_bot_token == bot_token).first()
    if not channel:
        return ChannelLookupResponse(found=False)
    tenant = channel.tenant
    if not tenant or tenant.status != TenantStatus.active:
        return ChannelLookupResponse(found=False)
    return ChannelLookupResponse(found=True, tenant=_build_tenant_context(tenant, channel))


@router.get("/resolve/whatsapp/{wa_number}", response_model=ChannelLookupResponse)
def resolve_by_wa_number(wa_number: str, db: Session = Depends(get_db)) -> ChannelLookupResponse:
    channel = db.query(TenantChannel).filter(TenantChannel.wa_number == wa_number).first()
    if not channel:
        return ChannelLookupResponse(found=False)
    tenant = channel.tenant
    if not tenant or tenant.status != TenantStatus.active:
        return ChannelLookupResponse(found=False)
    return ChannelLookupResponse(found=True, tenant=_build_tenant_context(tenant, channel))


@router.get("/resolve/webchat/{company_code}", response_model=ChannelLookupResponse)
def resolve_webchat(company_code: str, db: Session = Depends(get_db)) -> ChannelLookupResponse:
    channel = (
        db.query(TenantChannel)
        .join(Tenant, TenantChannel.tenant_id == Tenant.id)
        .filter(TenantChannel.webchat_widget_token == company_code, Tenant.status == TenantStatus.active)
        .first()
    )
    tenant: Tenant | None = channel.tenant if channel else None
    if not tenant:
        tenant = db.query(Tenant).filter(Tenant.company_code == company_code, Tenant.status == TenantStatus.active).first()
        channel = tenant.channel if tenant else None
    if not tenant or not channel or not channel.webchat_enabled:
        return ChannelLookupResponse(found=False)
    return ChannelLookupResponse(found=True, tenant=_build_tenant_context(tenant, channel))


@router.get("/tenants/{company_code}/buyers/by-phone/{phone}", response_model=BuyerLookupResponse)
def find_buyer_by_phone(company_code: str, phone: str, db: Session = Depends(get_db)) -> BuyerLookupResponse:
    tenant = _get_tenant(db, company_code)
    normalized_phone = _normalize_phone(phone)
    if not normalized_phone:
        return BuyerLookupResponse(found=False)
    identity = (
        db.query(BuyerChannelIdentity)
        .filter(BuyerChannelIdentity.tenant_id == tenant.id, BuyerChannelIdentity.phone == normalized_phone)
        .order_by(BuyerChannelIdentity.updated_at.desc())
        .first()
    )
    if identity and identity.erp_customer_id:
        return _buyer_lookup_response(
            tenant=tenant,
            erp_customer_id=identity.erp_customer_id,
            erp_customer_name=identity.full_name,
            identity=identity,
            phone=normalized_phone,
            recognized_via="phone_identity",
        )

    customer_id, customer_name = _resolve_erp_customer_by_phone(tenant, normalized_phone)
    if not customer_id:
        return BuyerLookupResponse(found=False)
    return _buyer_lookup_response(
        tenant=tenant,
        erp_customer_id=customer_id,
        erp_customer_name=customer_name,
        identity=identity,
        phone=normalized_phone,
        recognized_via="erp_phone",
    )


@router.get("/tenants/{company_code}/buyers/by-telegram/{tg_chat_id}", response_model=BuyerLookupResponse)
def find_buyer_by_telegram(company_code: str, tg_chat_id: str, db: Session = Depends(get_db)) -> BuyerLookupResponse:
    tenant = _get_tenant(db, company_code)
    identity = (
        db.query(BuyerChannelIdentity)
        .filter(
            BuyerChannelIdentity.tenant_id == tenant.id,
            BuyerChannelIdentity.channel == "telegram",
            BuyerChannelIdentity.channel_user_id == tg_chat_id,
        )
        .first()
    )
    if not identity:
        return BuyerLookupResponse(found=False)
    customer_id = identity.erp_customer_id
    customer_name = identity.full_name
    if identity.phone and not customer_id:
        customer_id, customer_name = _resolve_erp_customer_by_phone(tenant, identity.phone)
        if customer_id:
            identity.erp_customer_id = customer_id
            db.commit()
            db.refresh(identity)
    if not customer_id:
        return BuyerLookupResponse(found=False)
    return _buyer_lookup_response(
        tenant=tenant,
        erp_customer_id=customer_id,
        erp_customer_name=customer_name,
        identity=identity,
        recognized_via="channel_identity",
    )


@router.post("/tenants/{company_code}/buyers/resolve", response_model=BuyerLookupResponse)
def resolve_buyer(
    company_code: str,
    payload: ResolveBuyerRequest,
    db: Session = Depends(get_db),
) -> BuyerLookupResponse:
    tenant = _get_tenant(db, company_code)
    channel = str(payload.channel_type or "").strip().lower()
    channel_user_id = str(payload.channel_user_id or "").strip()
    if not channel or not channel_user_id:
        raise HTTPException(status_code=400, detail="channel_type and channel_user_id are required")

    identity = (
        db.query(BuyerChannelIdentity)
        .filter(
            BuyerChannelIdentity.tenant_id == tenant.id,
            BuyerChannelIdentity.channel == channel,
            BuyerChannelIdentity.channel_user_id == channel_user_id,
        )
        .first()
    )
    if identity and identity.erp_customer_id:
        return _buyer_lookup_response(
            tenant=tenant,
            erp_customer_id=identity.erp_customer_id,
            erp_customer_name=identity.full_name,
            identity=identity,
            recognized_via="channel_identity",
        )

    candidate_phone = payload.phone or (channel_user_id if channel == "whatsapp" else None)
    normalized_phone = _normalize_phone(candidate_phone)
    customer_id, customer_name = _resolve_erp_customer_by_phone(tenant, normalized_phone)
    if not customer_id:
        return BuyerLookupResponse(found=False)

    identity = _upsert_buyer_identity(
        db=db,
        tenant=tenant,
        channel=channel,
        channel_user_id=channel_user_id,
        erp_customer_id=customer_id,
        phone=normalized_phone,
        full_name=payload.full_name or customer_name,
    )
    db.commit()
    db.refresh(identity)
    return _buyer_lookup_response(
        tenant=tenant,
        erp_customer_id=customer_id,
        erp_customer_name=customer_name,
        identity=identity,
        phone=normalized_phone,
        recognized_via="phone_linked_to_channel",
    )


@router.get("/tenants/{company_code}/buyers/{erp_customer_id}/sales-history")
def get_buyer_sales_history(company_code: str, erp_customer_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    tenant = _get_tenant(db, company_code)
    sales_orders, sales_invoices = _load_sales_history(tenant, erp_customer_id)
    return {
        "erp_customer_id": erp_customer_id,
        "recent_sales_orders": sales_orders,
        "recent_sales_invoices": sales_invoices,
    }


@router.get("/tenants/{company_code}/ai-policy")
def get_ai_policy(company_code: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    tenant = _get_tenant(db, company_code)
    channel = tenant.channel
    return {
        "company_code": tenant.company_code,
        "ai_policy": _channel_ai_policy(channel, tenant),
    }


@router.post("/tenants/{company_code}/conversation-events", status_code=201)
def create_conversation_event(
    company_code: str,
    payload: AIConversationEventRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    tenant = _get_tenant(db, company_code)
    if payload.session_id and payload.channel_type and payload.channel_user_id:
        conversation = _get_or_create_conversation(
            db=db,
            tenant=tenant,
            session_id=payload.session_id,
            channel_type=payload.channel_type,
            channel_user_id=payload.channel_user_id,
            buyer_identity_id=payload.buyer_identity_id,
            stage=_safe_text(payload.payload_json.get("stage")) if isinstance(payload.payload_json, dict) else None,
        )
        _sync_conversation_runtime_state(
            conversation=conversation,
            payload=payload.payload_json,
            stage=_safe_text(payload.payload_json.get("stage")) if isinstance(payload.payload_json, dict) else None,
            event_type=payload.event_type,
        )
        db.commit()
    entry = _audit_ai_event(
        db,
        tenant,
        "ai_conversation_event",
        {
            "event_type": payload.event_type,
            "session_id": payload.session_id,
            "channel_type": payload.channel_type,
            "channel_user_id": payload.channel_user_id,
            "buyer_identity_id": payload.buyer_identity_id,
            "payload": payload.payload_json,
        },
    )
    return {"created": True, "event_id": str(entry.id)}


@router.post("/tenants/{company_code}/transcript/{session_id}/messages", status_code=201)
def create_transcript_message(
    company_code: str,
    session_id: str,
    payload: AITranscriptMessageRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    tenant = _get_tenant(db, company_code)
    conversation = _get_or_create_conversation(
        db=db,
        tenant=tenant,
        session_id=session_id,
        channel_type=payload.channel_type,
        channel_user_id=payload.channel_user_id,
        buyer_identity_id=payload.buyer_identity_id,
        erp_customer_id=payload.erp_customer_id,
        buyer_name=payload.buyer_name,
        buyer_phone=payload.buyer_phone,
        stage=payload.stage,
    )
    existing = (
        db.query(AIConversationMessage)
        .filter(
            AIConversationMessage.conversation_id == conversation.id,
            AIConversationMessage.message_id == payload.message_id,
        )
        .first()
    )
    if existing:
        return {"created": False, "message_id": str(existing.id), "deduplicated": True}

    message = AIConversationMessage(
        conversation_id=conversation.id,
        message_id=payload.message_id,
        role=str(payload.role or "").strip() or "assistant",
        message_type=str(payload.message_type or "chat").strip() or "chat",
        stage=str(payload.stage or "").strip() or None,
        behavior_class=str(payload.behavior_class or "").strip() or None,
        tool_name=str(payload.tool_name or "").strip() or None,
        content=payload.content,
        payload_json=payload.payload_json or {},
    )
    db.add(message)
    db.flush()
    if message.role == "user" and conversation.first_customer_message_at is None:
        conversation.first_customer_message_at = message.created_at
    conversation.last_message_at = message.created_at
    if payload.stage:
        conversation.last_stage = payload.stage
    if payload.message_type == "handoff":
        conversation.status = "handoff"
    elif payload.message_type == "closed":
        conversation.status = "closed"
    else:
        conversation.status = "open"
    _sync_conversation_runtime_state(
        conversation=conversation,
        payload=payload.payload_json,
        stage=payload.stage,
        message_type=payload.message_type,
        content=payload.content,
    )
    db.commit()
    db.refresh(message)
    return {"created": True, "message_id": str(message.id), "deduplicated": False}


@router.post("/tenants/{company_code}/handoffs", status_code=201)
def create_handoff(
    company_code: str,
    payload: AIHandoffRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    tenant = _get_tenant(db, company_code)
    channel = tenant.channel
    handoff_payload = payload.payload_json if isinstance(payload.payload_json, dict) else {}
    target_payload = handoff_payload if isinstance(handoff_payload, dict) else {}
    delivery = dispatch_handoff(
        tenant=tenant,
        channel=channel,
        target_type=str(target_payload.get("handoff_target_type") or "none"),
        destination=target_payload.get("handoff_target_destination"),
        instructions=target_payload.get("handoff_target_instructions"),
        reason=payload.reason,
        payload={
            "channel_type": payload.channel_type,
            "channel_user_id": payload.channel_user_id,
            "session_id": payload.session_id,
            "buyer_identity_id": payload.buyer_identity_id,
            **target_payload,
        },
    )
    if payload.session_id:
        conversation = _get_or_create_conversation(
            db=db,
            tenant=tenant,
            session_id=payload.session_id,
            channel_type=payload.channel_type,
            channel_user_id=payload.channel_user_id,
            buyer_identity_id=payload.buyer_identity_id,
        )
        _sync_conversation_runtime_state(
            conversation=conversation,
            payload=payload.payload_json,
            message_type="handoff",
            event_type="handoff_triggered",
            delivery=delivery,
        )
        db.commit()
    entry = _audit_ai_event(
        db,
        tenant,
        "ai_handoff",
        {
            "reason": payload.reason,
            "session_id": payload.session_id,
            "channel_type": payload.channel_type,
            "channel_user_id": payload.channel_user_id,
            "buyer_identity_id": payload.buyer_identity_id,
            "payload": payload.payload_json,
            "delivery": delivery,
        },
    )
    return {"created": True, "handoff_id": str(entry.id), "delivery": delivery}


@router.post("/tenants/{company_code}/buyers", response_model=BuyerLookupResponse, status_code=201)
def create_buyer(company_code: str, payload: CreateBuyerRequest, db: Session = Depends(get_db)) -> BuyerLookupResponse:
    tenant = _get_tenant(db, company_code)
    customer_id = ""
    customer_name = payload.full_name
    normalized_phone = _normalize_phone(payload.phone)

    if normalized_phone:
        existing = find_buyer_by_phone(company_code, normalized_phone, db)
        if existing.found and existing.erp_customer_id:
            customer_id = existing.erp_customer_id
            customer_name = existing.erp_customer_name or payload.full_name

    if not customer_id:
        customer_body: dict[str, Any] = {
            "customer_name": payload.full_name,
            "customer_type": "Individual",
            "customer_group": "Individual",
            "territory": "All Territories",
        }
        resp = _erp(tenant, "POST", "/api/resource/Customer", json_body=customer_body)
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"ERPNext Customer creation failed: {resp.text[:200]}")
        customer_doc = resp.json().get("data", {})
        customer_id = customer_doc.get("name", "")
        if normalized_phone:
            contact_body = {
                "full_name": payload.full_name,
                "mobile_no": normalized_phone,
                "links": [{"link_doctype": "Customer", "link_name": customer_id}],
            }
            _erp(tenant, "POST", "/api/resource/Contact", json_body=contact_body)

    channel_type = str(payload.channel_type or ("telegram" if payload.tg_chat_id else "")).strip().lower() or None
    channel_user_id = str(payload.channel_user_id or payload.tg_chat_id or "").strip() or None
    identity: BuyerChannelIdentity | None = None
    if channel_type and channel_user_id:
        identity = _upsert_buyer_identity(
            db=db,
            tenant=tenant,
            channel=channel_type,
            channel_user_id=channel_user_id,
            erp_customer_id=customer_id,
            phone=normalized_phone,
            full_name=payload.full_name,
        )
        db.commit()
        db.refresh(identity)

    return _buyer_lookup_response(
        tenant=tenant,
        erp_customer_id=customer_id,
        erp_customer_name=customer_name,
        identity=identity,
        phone=normalized_phone,
        channel=channel_type,
        recognized_via="buyer_created" if not identity else "channel_linked",
    )


@router.get("/tenants/{company_code}/items")
def get_items(
    company_code: str,
    item_group: str | None = None,
    item_name: str | None = None,
    lang: str | None = None,
    limit: int = 200,
    enrich: bool = True,
    db: Session = Depends(get_db),
) -> dict:
    tenant = _get_tenant(db, company_code)
    items = list_items(
        tenant,
        item_group=item_group,
        item_name=item_name,
        lang=lang,
        limit=limit,
        enrich=enrich,
    )
    return {"items": items}


@router.get("/tenants/{company_code}/items/{item_code}")
def get_item(company_code: str, item_code: str, lang: str | None = None, db: Session = Depends(get_db)) -> dict:
    tenant = _get_tenant(db, company_code)
    item = get_item_detail(tenant, item_code, lang=lang)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item '{item_code}' not found")
    return item


@router.post("/tenants/{company_code}/sales-orders", status_code=201)
def create_sales_order(company_code: str, payload: CreateSalesOrderRequest, db: Session = Depends(get_db)) -> dict:
    tenant = _get_tenant(db, company_code)
    return create_sales_order_for_tenant(
        tenant,
        customer=payload.customer,
        delivery_date=payload.delivery_date,
        items=payload.items,
    )


@router.get("/tenants/{company_code}/sales-orders/{sales_order_name}")
def get_sales_order(company_code: str, sales_order_name: str, db: Session = Depends(get_db)) -> dict:
    tenant = _get_tenant(db, company_code)
    return build_sales_order_summary(tenant, fetch_sales_order_doc(tenant, sales_order_name), sales_order_name)


@router.post("/tenants/{company_code}/sales-orders/{sales_order_name}/items")
def update_sales_order_items(
    company_code: str,
    sales_order_name: str,
    payload: UpdateSalesOrderRequest,
    db: Session = Depends(get_db),
) -> dict:
    tenant = _get_tenant(db, company_code)
    return update_sales_order_items_for_tenant(
        tenant,
        sales_order_name=sales_order_name,
        items=payload.items,
    )


@router.post("/tenants/{company_code}/invoices", status_code=201)
def create_invoice(company_code: str, payload: CreateInvoiceRequest, db: Session = Depends(get_db)) -> dict:
    tenant = _get_tenant(db, company_code)
    return create_invoice_from_sales_order(tenant, sales_order_name=payload.sales_order_name)


@router.post("/tenants/{company_code}/licenses", status_code=201)
def create_license(company_code: str, payload: CreateLicenseRequest, db: Session = Depends(get_db)) -> dict:
    tenant = _get_tenant(db, company_code)
    raw_key = secrets.token_urlsafe(32)
    fp = fingerprint_license_key(raw_key) or None
    entry = LicenseKey(
        tenant_id=tenant.id,
        hashed_key=hash_license_key(raw_key),
        fingerprint=fp,
        status=LicenseKeyStatus.active,
        description=payload.description,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": str(entry.id), "license_key": raw_key, "status": entry.status.value}


@router.patch("/tenants/{company_code}/subscription")
def extend_subscription(company_code: str, payload: ExtendSubscriptionRequest, db: Session = Depends(get_db)) -> dict:
    tenant = _get_tenant(db, company_code)
    now = utcnow()
    base = tenant.subscription_expires_at if tenant.subscription_expires_at > now else now
    tenant.subscription_expires_at = base + timedelta(days=payload.add_days)
    db.commit()
    return {"company_code": tenant.company_code, "subscription_expires_at": tenant.subscription_expires_at.isoformat()}
