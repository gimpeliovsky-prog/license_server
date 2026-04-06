from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from app.config import get_settings
from app.models.tenant import Tenant
from app.models.tenant_channel import TenantChannel

logger = logging.getLogger(__name__)


def _safe_text(value: Any) -> str:
    text = str(value or "").strip()
    return text or "-"


def _build_message_text(*, tenant: Tenant, payload: dict[str, Any], reason: str | None) -> str:
    lines = [
        "AI sales handoff",
        "",
        f"Tenant: {_safe_text(tenant.company_code)}",
        f"Company: {_safe_text(tenant.company_name)}",
        f"Reason: {_safe_text(reason)}",
        f"Channel: {_safe_text(payload.get('channel_type'))}",
        f"Channel user: {_safe_text(payload.get('channel_user_id'))}",
        f"Session: {_safe_text(payload.get('session_id'))}",
        f"Buyer: {_safe_text(payload.get('buyer_name'))}",
        f"ERP customer: {_safe_text(payload.get('erp_customer_id'))}",
        f"Active order: {_safe_text(payload.get('active_order_name'))}",
        f"Reply preview: {_safe_text(payload.get('reply_preview'))}",
        "",
        "Operator instructions:",
        _safe_text(payload.get("handoff_target_instructions")),
    ]
    return "\n".join(lines)


def _dispatch_email(*, destination: str, subject: str, body: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from_email:
        raise RuntimeError("SMTP is not configured on License Server.")

    recipients = [item.strip() for item in destination.split(",") if item.strip()]
    if not recipients:
        raise RuntimeError("No email recipients configured for the handoff target.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_email
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)

    return {"status": "delivered", "mode": "email", "recipient_count": len(recipients)}


def _dispatch_telegram(*, bot_token: str, destination: str, body: str) -> dict[str, Any]:
    if not bot_token:
        raise RuntimeError("Telegram handoff target requires a configured tenant Telegram bot token.")
    chat_id = str(destination or "").strip()
    if not chat_id:
        raise RuntimeError("Telegram handoff target requires an operator chat id.")

    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": body,
                "disable_web_page_preview": True,
            },
        )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API rejected handoff delivery: {payload}")

    return {"status": "delivered", "mode": "telegram", "chat_id": chat_id}


def dispatch_handoff(
    *,
    tenant: Tenant,
    channel: TenantChannel | None,
    target_type: str,
    destination: str | None,
    instructions: str | None,
    reason: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    normalized_target = str(target_type or "none").strip().lower() or "none"
    target_destination = str(destination or "").strip() or None

    delivery_payload = {
        "target_type": normalized_target,
        "target_destination": target_destination,
        "instructions": instructions,
        "attempted": normalized_target != "none",
        "status": "queued" if normalized_target == "dashboard" else "not_configured",
        "details": None,
        "error": None,
    }
    if normalized_target in {"none", "dashboard"}:
        return delivery_payload

    body_payload = dict(payload)
    body_payload["handoff_target_instructions"] = instructions
    body = _build_message_text(tenant=tenant, payload=body_payload, reason=reason)
    subject = f"{get_settings().ai_handoff_email_subject_prefix} {tenant.company_code} {reason or 'general'}".strip()

    try:
        if normalized_target == "email":
            delivery_payload["details"] = _dispatch_email(
                destination=target_destination or "",
                subject=subject,
                body=body,
            )
            delivery_payload["status"] = "delivered"
            return delivery_payload
        if normalized_target == "telegram":
            bot_token = channel.tg_bot_token if channel else ""
            delivery_payload["details"] = _dispatch_telegram(
                bot_token=bot_token or "",
                destination=target_destination or "",
                body=body,
            )
            delivery_payload["status"] = "delivered"
            return delivery_payload
        delivery_payload["status"] = "unsupported"
        delivery_payload["error"] = f"Unsupported handoff target type '{normalized_target}'."
        return delivery_payload
    except Exception as exc:
        logger.warning("AI handoff delivery failed for tenant %s: %s", tenant.company_code, exc)
        delivery_payload["status"] = "failed"
        delivery_payload["error"] = str(exc)
        return delivery_payload
