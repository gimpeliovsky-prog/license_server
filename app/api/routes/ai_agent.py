"""API for AI Sales Manager."""

from __future__ import annotations

import json
import secrets
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_ai_agent
from app.models import AIConversation, AIConversationMessage, AuditLog, BuyerChannelIdentity
from app.models import LicenseKey, LicenseKeyStatus, Tenant, TenantStatus
from app.models.tenant_channel import TenantChannel
from app.services.ai_handoff import dispatch_handoff
from app.services.erp_catalog import get_item_detail, list_items
from app.services.erp_customers import (
    create_individual_customer,
    get_customer_detail as get_customer_detail_for_tenant,
    list_customers as list_customers_for_tenant,
    load_sales_history,
    resolve_customer_by_phone,
)
from app.services.erp_media import fetch_private_file, fetch_public_file
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
from app.services.erp_stock import (
    get_item_availability as get_item_availability_for_tenant,
    get_sales_order_status as get_sales_order_status_for_tenant,
    get_stock_settings as get_stock_settings_for_tenant,
    list_warehouses as list_warehouses_for_tenant,
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
    sales_orders, sales_invoices = load_sales_history(tenant, erp_customer_id)
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
        "get_item_availability",
        "register_buyer",
        "get_buyer_sales_history",
        "create_sales_order",
        "get_sales_order_status",
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
    if any(tool in allowed_tools for tool in {"update_sales_order", "send_sales_order_pdf", "create_invoice"}):
        if "get_sales_order_status" not in allowed_tools:
            allowed_tools.append("get_sales_order_status")
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

    customer_id, customer_name = resolve_customer_by_phone(tenant, normalized_phone)
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
        customer_id, customer_name = resolve_customer_by_phone(tenant, _normalize_phone(identity.phone))
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
    customer_id, customer_name = resolve_customer_by_phone(tenant, normalized_phone)
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
    sales_orders, sales_invoices = load_sales_history(tenant, erp_customer_id)
    return {
        "erp_customer_id": erp_customer_id,
        "recent_sales_orders": sales_orders,
        "recent_sales_invoices": sales_invoices,
    }


@router.get("/tenants/{company_code}/customers")
def get_customers(
    company_code: str,
    limit_start: int | None = None,
    limit_page_length: int | None = 200,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    tenant = _get_tenant(db, company_code)
    return {
        "data": list_customers_for_tenant(
            tenant,
            fields='["name","customer_name"]',
            limit_start=limit_start,
            limit_page_length=limit_page_length,
        )
    }


@router.get("/tenants/{company_code}/customers/{erp_customer_id}")
def get_customer(company_code: str, erp_customer_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    tenant = _get_tenant(db, company_code)
    data = get_customer_detail_for_tenant(tenant, erp_customer_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Customer '{erp_customer_id}' not found")
    return data


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
        try:
            customer_id, customer_name = create_individual_customer(
                tenant,
                full_name=payload.full_name,
                normalized_phone=normalized_phone,
            )
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

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


@router.get("/tenants/{company_code}/items/{item_code}/availability")
def get_item_availability(
    company_code: str,
    item_code: str,
    warehouse: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    tenant = _get_tenant(db, company_code)
    return get_item_availability_for_tenant(tenant, item_code=item_code, warehouse=warehouse)


@router.get("/tenants/{company_code}/files/{file_path:path}")
def get_public_media(company_code: str, file_path: str, db: Session = Depends(get_db)) -> Response:
    tenant = _get_tenant(db, company_code)
    try:
        response = fetch_public_file(tenant, file_path)
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


@router.get("/tenants/{company_code}/private/files/{file_path:path}")
def get_private_media(company_code: str, file_path: str, db: Session = Depends(get_db)) -> Response:
    tenant = _get_tenant(db, company_code)
    try:
        response = fetch_private_file(tenant, file_path)
    except ERPNextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


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


@router.get("/tenants/{company_code}/sales-orders/{sales_order_name}/status")
def get_sales_order_status(company_code: str, sales_order_name: str, db: Session = Depends(get_db)) -> dict:
    tenant = _get_tenant(db, company_code)
    return get_sales_order_status_for_tenant(tenant, sales_order_name)


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


@router.get("/tenants/{company_code}/stock-settings")
def get_stock_settings(company_code: str, db: Session = Depends(get_db)) -> dict:
    tenant = _get_tenant(db, company_code)
    return {"data": get_stock_settings_for_tenant(tenant, fields='["default_warehouse"]')}


@router.get("/tenants/{company_code}/warehouses")
def get_warehouses(company_code: str, limit: int = 200, db: Session = Depends(get_db)) -> dict:
    tenant = _get_tenant(db, company_code)
    return {"data": list_warehouses_for_tenant(tenant, fields='["name"]', limit_page_length=limit)}


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
