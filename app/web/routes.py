import hmac
import json
import secrets
import uuid
from datetime import date, datetime, time, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import case
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes.admin import diagnose_license as api_diagnose_license
from app.api.deps import get_client_ip, get_db
from app.config import get_settings
from app.models import (
    AIConversation,
    AIConversationMessage,
    AuditLog,
    Device,
    ERPAllowlistEntry,
    ERPAllowlistType,
    LicenseKey,
    LicenseKeyStatus,
    OTAAccess,
    ProcessJob,
    Tenant,
    TenantChannel,
    TenantStatus,
)
from app.models.firmware import DeviceOTALog, Firmware
from app.services.allowlist import (
    MOBILE_APP_DOCTYPES,
    has_allowlist_entries,
    normalize_doctype,
    normalize_method,
    seed_allowlist_mobile_profile,
    seed_allowlist_from_settings,
)
from app.services.rate_limit import RateLimiter
from app.services.erpnext import (
    ERPNextError,
    ERPNextValidationError,
    normalize_erpnext_url,
    request_erpnext,
    validate_erpnext_credentials,
)
from app.services.license import fingerprint_license_key, hash_license_key
from app.services.ota import OTAService
from app.services.ota_binary import parse_esp_app_desc_version
from app.services.process_jobs import (
    cleanup_process_jobs,
    create_process_job,
    list_process_jobs,
    normalize_process_job_query,
    normalize_process_job_status,
    parse_process_job_statuses,
    requeue_process_job,
)
from app.services.process_job_runner import notify_process_job_runner
from app.services.tenant_cleanup import delete_tenant_with_dependencies
from app.schemas.admin import LicenseDiagnosticsRequest
from app.schemas.tenant_config import TenantConfigPayload, TenantConfigSnapshot
from app.utils.time import utcnow

router = APIRouter(prefix="/admin-ui", tags=["admin-ui"])
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()
login_limiter = RateLimiter(settings.rate_limit_login_per_minute, 60)
ota_service = OTAService(firmware_base_path="firmware")


def parse_datetime_input(value: str) -> datetime:
    if "T" not in value:
        parsed_date = date.fromisoformat(value)
        parsed = datetime.combine(parsed_date, time(23, 59, 59))
    else:
        parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_tenant_status(value: str) -> TenantStatus | None:
    try:
        return TenantStatus(value)
    except ValueError:
        return None


def parse_license_status(value: str) -> LicenseKeyStatus | None:
    try:
        return LicenseKeyStatus(value)
    except ValueError:
        return None


def parse_bool_form(value) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_license_description(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def normalize_phone(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def normalize_json_array(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return json.dumps([str(item).strip() for item in parsed if str(item).strip()], ensure_ascii=False)


def parse_json_array(value: str | None) -> list[str] | None:
    if value is None:
        return []
    normalized = value.strip()
    if not normalized:
        return []
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    result: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        text = str(item).strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def parse_json_object(value: str | None) -> dict | list | None:
    if value is None:
        return {}
    normalized = value.strip()
    if not normalized:
        return {}
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, (dict, list)):
        return None
    return parsed


def _safe_json_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _default_tenant_config_payload() -> dict:
    return {
        "access": {
            "client_profile": "STANDARD",
            "pick_list_images_enabled": False,
            "erp_custom_fields_enabled": False,
            "box_count_feature_enabled": False,
            "or_qty_label": None,
            "or_qty_required": True,
        },
        "barcodes": [],
        "scales": {
            "scale_enabled": False,
            "scale_unit": "kg",
            "hosts": [],
        },
        "ai": {
            "allow_invoice": True,
            "allow_license_ops": True,
            "allow_discount_promises": False,
            "allow_free_text_catalog_answers": True,
            "handoff_rules": {
                "enabled": True,
                "clarification_failure_limit": 2,
                "allow_customer_requested_handoff": True,
                "frustrated_customer_handoff": True,
            },
            "handoff_target": {
                "target_type": "none",
                "destination": None,
                "instructions": None,
            },
            "tools_policy": {
                "allowed_tools": [],
            },
            "classification": {
                "behavior_rules": [],
                "intent_rules": [],
            },
            "prompt_overrides": {
                "core_policy": [],
                "language_policy": [],
                "catalog_policy": [],
                "order_policy": [],
                "service_policy": [],
                "stage_prompts": {},
                "behavior_prompts": {},
                "channel_prompts": {},
                "handoff_messages": {},
            },
        },
    }


def _sanitize_tenant_config_payload(payload: dict) -> dict:
    sanitized = dict(payload)
    sanitized.pop("fulfillment_rules", None)
    return sanitized


def build_tenant_config_snapshot(tenant: Tenant) -> TenantConfigSnapshot:
    payload = _sanitize_tenant_config_payload(_safe_json_dict(tenant.tenant_config))
    merged_payload = _default_tenant_config_payload()
    merged_payload.update(payload)
    if isinstance(payload.get("access"), dict):
        merged_payload["access"] = {**merged_payload["access"], **payload["access"]}
    if isinstance(payload.get("scales"), dict):
        merged_payload["scales"] = {**merged_payload["scales"], **payload["scales"]}
    if isinstance(payload.get("barcodes"), list):
        merged_payload["barcodes"] = payload["barcodes"]
    if isinstance(payload.get("ai"), dict):
        merged_payload["ai"] = {**merged_payload["ai"], **payload["ai"]}
        ai_payload = payload["ai"]
        if isinstance(ai_payload.get("handoff_rules"), dict):
            merged_payload["ai"]["handoff_rules"] = {
                **merged_payload["ai"]["handoff_rules"],
                **ai_payload["handoff_rules"],
            }
        if isinstance(ai_payload.get("handoff_target"), dict):
            merged_payload["ai"]["handoff_target"] = {
                **merged_payload["ai"]["handoff_target"],
                **ai_payload["handoff_target"],
            }
        if isinstance(ai_payload.get("tools_policy"), dict):
            merged_payload["ai"]["tools_policy"] = {
                **merged_payload["ai"]["tools_policy"],
                **ai_payload["tools_policy"],
            }
        if isinstance(ai_payload.get("classification"), dict):
            merged_payload["ai"]["classification"] = {
                **merged_payload["ai"]["classification"],
                **ai_payload["classification"],
            }
        if isinstance(ai_payload.get("prompt_overrides"), dict):
            merged_payload["ai"]["prompt_overrides"] = {
                **merged_payload["ai"]["prompt_overrides"],
                **ai_payload["prompt_overrides"],
            }

    return TenantConfigSnapshot(
        config_revision=(tenant.tenant_config_revision or "").strip(),
        settings_scope="tenant",
        updated_at=tenant.tenant_config_updated_at.isoformat() if tenant.tenant_config_updated_at else None,
        updated_by=(tenant.tenant_config_updated_by or "").strip() or None,
        payload=merged_payload,
    )


def fetch_tenant_doctypes(tenant: Tenant) -> list[dict[str, object]]:
    fields = json.dumps(["name", "module", "custom", "issingle", "istable"], separators=(",", ":"))
    limit = 500
    start = 0
    rows: list[dict[str, object]] = []

    while True:
        response = request_erpnext(
            tenant.erpnext_url,
            tenant.api_key,
            tenant.api_secret,
            "GET",
            "/api/resource/DocType",
            params={
                "fields": fields,
                "limit_page_length": limit,
                "limit_start": start,
                "order_by": "name asc",
            },
        )
        if response.status_code >= 400:
            detail = response.text.strip()
            try:
                parsed = _safe_json_dict(response.json())
                message = parsed.get("message")
                exc = parsed.get("exc")
                if isinstance(message, str) and message.strip():
                    detail = message.strip()
                elif isinstance(exc, str) and exc.strip():
                    detail = exc.strip()
            except Exception:
                pass
            raise HTTPException(
                status_code=502,
                detail=f"ERP DocType fetch failed (HTTP {response.status_code}): {detail or 'unknown error'}",
            )

        payload = _safe_json_dict(response.json())
        data = payload.get("data")
        if not isinstance(data, list):
            break

        batch: list[dict[str, object]] = []
        for item in data:
            row = _safe_json_dict(item)
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            batch.append(
                {
                    "name": name,
                    "module": str(row.get("module") or "").strip(),
                    "custom": bool(row.get("custom")),
                    "issingle": bool(row.get("issingle")),
                    "istable": bool(row.get("istable")),
                }
            )

        rows.extend(batch)
        if len(data) < limit:
            break
        start += len(data)
        if start >= 10000:
            break

    rows.sort(key=lambda item: str(item.get("name") or "").lower())
    return rows


def is_admin(request: Request) -> bool:
    return bool(request.session.get("is_admin"))


def validate_admin_session(request: Request) -> bool:
    if not is_admin(request):
        return False
    now_ts = int(utcnow().timestamp())
    login_at = request.session.get("login_at")
    last_seen = request.session.get("last_seen")
    max_age = settings.admin_session_max_age_seconds
    idle = settings.admin_session_idle_seconds

    try:
        login_at = int(login_at)
        last_seen = int(last_seen)
    except (TypeError, ValueError):
        request.session.clear()
        return False

    if max_age > 0 and now_ts - login_at > max_age:
        request.session.clear()
        set_flash(request, error="Admin session expired. Please log in again.")
        return False
    if idle > 0 and now_ts - last_seen > idle:
        request.session.clear()
        set_flash(request, error="Admin session timed out. Please log in again.")
        return False

    request.session["last_seen"] = now_ts
    return True


def require_admin_or_redirect(request: Request):
    if not validate_admin_session(request):
        return RedirectResponse("/admin-ui/login", status_code=303)
    return None


def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def require_csrf(request: Request, form: dict, redirect_path: str) -> RedirectResponse | None:
    token = str(form.get("csrf_token") or "").strip()
    if not token or token != request.session.get("csrf_token"):
        set_flash(request, error="Invalid CSRF token")
        return redirect_to(redirect_path)
    return None


def set_flash(request: Request, message: str | None = None, error: str | None = None, license_key: str | None = None):
    if message:
        request.session["flash_message"] = message
    if error:
        request.session["flash_error"] = error
    if license_key:
        request.session["flash_license_key"] = license_key


def pop_flash(request: Request) -> tuple[str | None, str | None, str | None]:
    message = request.session.pop("flash_message", None)
    error = request.session.pop("flash_error", None)
    license_key = request.session.pop("flash_license_key", None)
    return message, error, license_key


def redirect_to(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def get_tenant_or_none(db: Session, company_code: str) -> Tenant | None:
    return db.query(Tenant).filter(Tenant.company_code == company_code).first()


def _session_tenant(db: Session, request: Request) -> Tenant | None:
    tenant_id = str(request.session.get("tenant_id") or "").strip()
    if not tenant_id:
        return None
    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except ValueError:
        return None
    return db.query(Tenant).filter(Tenant.id == tenant_uuid).first()


def build_admin_context(
    request: Request,
    title: str,
    active_context: str,
    active_page: str,
    **extra,
) -> dict:
    context = {
        "request": request,
        "title": title,
        "is_admin": True,
        "active_context": active_context,
        "active_page": active_page,
    }
    context.update(extra)
    return context


LOG_SCOPE_OPTIONS = {"auth", "diagnostics", "all"}
LOG_OUTCOME_OPTIONS = {"all", "failed", "success"}
AI_LOG_SCOPE_OPTIONS = {"handoffs", "events", "all"}
AUTH_LOG_ACTIONS = {
    "activate",
    "activate_failed",
    "activate_erp_failed",
    "pairing_activate",
    "pairing_activate_failed",
    "validate_license_failed",
}
DIAGNOSTICS_LOG_ACTIONS = {"license_diagnostics"}
AI_AUDIT_ACTIONS = {"ai_handoff", "ai_conversation_event"}
ACTION_LABELS = {
    "activate": "Activate",
    "activate_failed": "Activate",
    "activate_erp_failed": "Activate ERP",
    "pairing_activate": "Pairing activate",
    "pairing_activate_failed": "Pairing activate",
    "validate_license_failed": "Validate license",
    "license_diagnostics": "License diagnostics",
}
def parse_log_window_hours(value: str | None, default: int = 24) -> int:
    try:
        parsed = int(str(value or default).strip())
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    return min(parsed, 24 * 30)


def normalize_log_scope(value: str | None) -> str:
    normalized = str(value or "auth").strip().lower()
    return normalized if normalized in LOG_SCOPE_OPTIONS else "auth"


def normalize_log_outcome(value: str | None) -> str:
    normalized = str(value or "all").strip().lower()
    return normalized if normalized in LOG_OUTCOME_OPTIONS else "all"


def normalize_ai_log_scope(value: str | None) -> str:
    normalized = str(value or "all").strip().lower()
    return normalized if normalized in AI_LOG_SCOPE_OPTIONS else "all"


def normalize_ai_channel_type(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"telegram", "whatsapp", "webchat"} else ""


def normalize_ai_conversation_status(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"open", "handoff", "closed"} else ""


def normalize_ai_stage_filter(value: str | None) -> str:
    return str(value or "").strip()


def normalize_free_text_filter(value: str | None) -> str:
    return str(value or "").strip()


def normalize_ai_operations_view(value: str | None) -> str:
    normalized = str(value or "all").strip().lower()
    return normalized if normalized in {"all", "failed-deliveries", "handoffs"} else "all"


def normalize_ai_conversations_view(value: str | None) -> str:
    normalized = str(value or "all").strip().lower()
    return normalized if normalized in {"all", "needs-attention", "open", "closed"} else "all"


def normalize_delivery_status(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"delivered", "failed", "not_configured", "skipped", "pending"} else ""

def parse_auto_refresh(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _event_result(action: str, meta: dict) -> str:
    if action.endswith("_failed"):
        return "failed"
    if action == "license_diagnostics":
        valid = meta.get("valid")
        if valid is True:
            return "success"
        if valid is False:
            return "failed"
    return "success"


def fetch_license_event_logs(
    db: Session,
    *,
    window_hours: int,
    scope: str,
    outcome: str,
    limit: int = 200,
) -> list[dict]:
    if scope == "diagnostics":
        actions = DIAGNOSTICS_LOG_ACTIONS
    elif scope == "all":
        actions = AUTH_LOG_ACTIONS | DIAGNOSTICS_LOG_ACTIONS
    else:
        actions = AUTH_LOG_ACTIONS

    since_time = utcnow() - timedelta(hours=window_hours)
    records = (
        db.query(AuditLog)
        .filter(AuditLog.created_at >= since_time)
        .filter(AuditLog.action.in_(sorted(actions)))
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )

    tenant_ids = list({record.tenant_id for record in records if record.tenant_id})
    tenant_codes: dict[uuid.UUID, str] = {}
    if tenant_ids:
        tenants = db.query(Tenant.id, Tenant.company_code).filter(Tenant.id.in_(tenant_ids)).all()
        tenant_codes = {tenant_id: company_code for tenant_id, company_code in tenants}

    rows: list[dict] = []
    for record in records:
        meta = record.meta if isinstance(record.meta, dict) else {}
        result = _event_result(record.action, meta)
        if outcome == "failed" and result != "failed":
            continue
        if outcome == "success" and result != "success":
            continue

        reason = (
            meta.get("reason_detail")
            or meta.get("detail")
            or meta.get("reason_code")
            or "-"
        )
        rows.append(
            {
                "created_at": record.created_at,
                "action": record.action,
                "event_label": ACTION_LABELS.get(record.action, record.action),
                "result": result,
                "reason": reason,
                "tenant_id": record.tenant_id,
                "tenant_company_code": tenant_codes.get(record.tenant_id),
                "company_code": (
                    meta.get("attempted_company_code")
                    or meta.get("input_company_code")
                    or tenant_codes.get(record.tenant_id)
                    or "-"
                ),
                "erp_username": meta.get("attempted_erp_username") or meta.get("erp_username") or "-",
                "device_id": meta.get("attempted_device_id") or meta.get("used_device_id") or "-",
                "key_fingerprint": meta.get("key_fingerprint") or "-",
                "ip": meta.get("ip") or "-",
                "status_code": meta.get("status_code"),
            }
        )
    return rows


def fetch_ai_audit_logs(
    db: Session,
    *,
    window_hours: int,
    scope: str,
    tenant_code: str | None = None,
    channel_type: str | None = None,
    session_id: str | None = None,
    event_type: str | None = None,
    delivery_status: str | None = None,
    query: str | None = None,
    limit: int = 200,
) -> list[dict]:
    if scope == "handoffs":
        actions = {"ai_handoff"}
    elif scope == "events":
        actions = {"ai_conversation_event"}
    else:
        actions = AI_AUDIT_ACTIONS

    since_time = utcnow() - timedelta(hours=window_hours)
    record_query = (
        db.query(AuditLog)
        .filter(AuditLog.created_at >= since_time)
        .filter(AuditLog.action.in_(sorted(actions)))
        .order_by(AuditLog.created_at.desc())
    )
    if tenant_code:
        tenant_ids = [
            tenant_id
            for tenant_id, in db.query(Tenant.id).filter(Tenant.company_code == tenant_code).all()
        ]
        if not tenant_ids:
            return []
        record_query = record_query.filter(AuditLog.tenant_id.in_(tenant_ids))

    raw_limit = limit
    if channel_type or session_id or event_type or delivery_status or query:
        raw_limit = max(limit * 8, 1000)
    records = record_query.limit(raw_limit).all()

    tenant_ids = list({record.tenant_id for record in records if record.tenant_id})
    tenant_codes: dict[uuid.UUID, str] = {}
    if tenant_ids:
        tenants = db.query(Tenant.id, Tenant.company_code).filter(Tenant.id.in_(tenant_ids)).all()
        tenant_codes = {tenant_id: company_code for tenant_id, company_code in tenants}

    normalized_query = str(query or "").strip().lower()
    rows: list[dict] = []
    conversation_keys: set[tuple[uuid.UUID, str]] = set()
    for record in records:
        meta = record.meta if isinstance(record.meta, dict) else {}
        payload = meta.get("payload") if isinstance(meta.get("payload"), dict) else {}
        delivery = meta.get("delivery") if isinstance(meta.get("delivery"), dict) else {}
        event_type = str(meta.get("event_type") or "").strip()
        record_channel_type = str(meta.get("channel_type") or "").strip() or "-"
        channel_user_id = str(meta.get("channel_user_id") or "").strip() or "-"
        record_session_id = str(meta.get("session_id") or "").strip() or "-"
        reason = str(meta.get("reason") or payload.get("reason") or payload.get("handoff_reason") or "").strip() or "-"
        active_order_name = (
            payload.get("active_order_name")
            or payload.get("name")
            or payload.get("sales_order_name")
            or "-"
        )
        row = {
            "created_at": record.created_at,
            "action": record.action,
            "event_type": event_type or record.action,
            "tenant_id": record.tenant_id,
            "tenant_company_code": tenant_codes.get(record.tenant_id) or "-",
            "channel_type": record_channel_type,
            "channel_user_id": channel_user_id,
            "session_id": record_session_id,
            "reason": reason,
            "active_order_name": active_order_name,
            "payload": payload,
            "target_type": payload.get("handoff_target_type") or "-",
            "target_destination": payload.get("handoff_target_destination") or "-",
            "delivery_status": delivery.get("status") or "-",
            "delivery_error": delivery.get("error") or "-",
            "reply_preview": payload.get("reply_preview") or payload.get("next_action") or "-",
            "conversation_id": None,
        }
        if channel_type and row["channel_type"] != channel_type:
            continue
        if session_id and row["session_id"] != session_id:
            continue
        if event_type and row["event_type"] != event_type:
            continue
        if delivery_status and row["delivery_status"] != delivery_status:
            continue
        if normalized_query:
            haystack = " ".join(
                [
                    str(row["tenant_company_code"]),
                    str(row["channel_type"]),
                    str(row["channel_user_id"]),
                    str(row["session_id"]),
                    str(row["reason"]),
                    str(row["event_type"]),
                    str(row["active_order_name"]),
                    str(row["target_type"]),
                    str(row["target_destination"]),
                    str(row["delivery_status"]),
                    str(row["delivery_error"]),
                    str(row["reply_preview"]),
                ]
            ).lower()
            if normalized_query not in haystack:
                continue
        rows.append(row)
        if record.tenant_id and row["session_id"] != "-":
            conversation_keys.add((record.tenant_id, row["session_id"]))
        if len(rows) >= limit:
            break

    if conversation_keys:
        conversation_rows = (
            db.query(AIConversation.id, AIConversation.tenant_id, AIConversation.session_id)
            .filter(
                AIConversation.tenant_id.in_([tenant_id for tenant_id, _ in conversation_keys]),
                AIConversation.session_id.in_([current_session_id for _, current_session_id in conversation_keys]),
            )
            .all()
        )
        conversation_ids = {
            (tenant_id, current_session_id): str(conversation_id)
            for conversation_id, tenant_id, current_session_id in conversation_rows
        }
        for row in rows:
            if row["tenant_id"] and row["session_id"] != "-":
                row["conversation_id"] = conversation_ids.get((row["tenant_id"], row["session_id"]))
    return rows


def list_ai_conversations(
    db: Session,
    *,
    limit: int = 200,
    tenant_code: str | None = None,
    channel_type: str | None = None,
    status: str | None = None,
    last_stage: str | None = None,
    query: str | None = None,
) -> list[dict]:
    q = db.query(AIConversation, Tenant.company_code).join(Tenant, AIConversation.tenant_id == Tenant.id)
    if tenant_code:
        q = q.filter(Tenant.company_code == tenant_code)
    if channel_type:
        q = q.filter(AIConversation.channel_type == channel_type)
    if status:
        q = q.filter(AIConversation.status == status)
    if last_stage:
        q = q.filter(AIConversation.last_stage == last_stage)
    if query:
        like_query = f"%{query}%"
        q = q.filter(
            (AIConversation.session_id.ilike(like_query))
            | (AIConversation.channel_user_id.ilike(like_query))
            | (AIConversation.erp_customer_id.ilike(like_query))
            | (AIConversation.buyer_name.ilike(like_query))
            | (AIConversation.buyer_phone.ilike(like_query))
        )
    rows = (
        q.order_by(
            case(
                (AIConversation.status == "handoff", 0),
                (AIConversation.status == "open", 1),
                else_=2,
            ),
            AIConversation.last_message_at.desc().nullslast(),
            AIConversation.created_at.desc(),
        )
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(conversation.id),
            "tenant_company_code": company_code,
            "session_id": conversation.session_id,
            "channel_type": conversation.channel_type,
            "channel_user_id": conversation.channel_user_id,
            "erp_customer_id": conversation.erp_customer_id,
            "buyer_name": conversation.buyer_name,
            "buyer_phone": conversation.buyer_phone,
            "status": conversation.status,
            "last_stage": conversation.last_stage,
            "summary": conversation.summary,
            "last_message_at": conversation.last_message_at,
            "created_at": conversation.created_at,
        }
        for conversation, company_code in rows
    ]


def list_ai_conversation_stage_options(
    db: Session,
    *,
    tenant_code: str | None = None,
    channel_type: str | None = None,
    status: str | None = None,
) -> list[str]:
    q = db.query(AIConversation.last_stage).join(Tenant, AIConversation.tenant_id == Tenant.id)
    if tenant_code:
        q = q.filter(Tenant.company_code == tenant_code)
    if channel_type:
        q = q.filter(AIConversation.channel_type == channel_type)
    if status:
        q = q.filter(AIConversation.status == status)
    rows = q.filter(AIConversation.last_stage.isnot(None)).distinct().order_by(AIConversation.last_stage.asc()).all()
    return [str(last_stage) for last_stage, in rows if str(last_stage or "").strip()]


def list_ai_audit_event_type_options(
    db: Session,
    *,
    window_hours: int,
    scope: str,
    tenant_code: str | None = None,
    channel_type: str | None = None,
) -> list[str]:
    rows = fetch_ai_audit_logs(
        db=db,
        window_hours=window_hours,
        scope=scope,
        tenant_code=tenant_code,
        channel_type=channel_type,
        limit=500,
    )
    values = sorted({str(row["event_type"]) for row in rows if str(row.get("event_type") or "").strip()})
    return values


def get_ai_conversation_detail(db: Session, conversation_id: str) -> tuple[dict | None, list[dict]]:
    try:
        conversation_uuid = uuid.UUID(conversation_id)
    except ValueError:
        return None, []
    row = (
        db.query(AIConversation, Tenant.company_code)
        .join(Tenant, AIConversation.tenant_id == Tenant.id)
        .filter(AIConversation.id == conversation_uuid)
        .first()
    )
    if not row:
        return None, []
    conversation, company_code = row
    messages = (
        db.query(AIConversationMessage)
        .filter(AIConversationMessage.conversation_id == conversation.id)
        .order_by(AIConversationMessage.created_at.asc(), AIConversationMessage.id.asc())
        .all()
    )
    return (
        {
            "id": str(conversation.id),
            "tenant_company_code": company_code,
            "session_id": conversation.session_id,
            "channel_type": conversation.channel_type,
            "channel_user_id": conversation.channel_user_id,
            "erp_customer_id": conversation.erp_customer_id,
            "buyer_name": conversation.buyer_name,
            "buyer_phone": conversation.buyer_phone,
            "status": conversation.status,
            "last_stage": conversation.last_stage,
            "first_customer_message_at": conversation.first_customer_message_at,
            "last_message_at": conversation.last_message_at,
            "created_at": conversation.created_at,
            "summary": conversation.summary,
        },
        [
            {
                "id": str(message.id),
                "message_id": message.message_id,
                "role": message.role,
                "message_type": message.message_type,
                "stage": message.stage,
                "behavior_class": message.behavior_class,
                "tool_name": message.tool_name,
                "content": message.content,
                "payload_json": message.payload_json,
                "created_at": message.created_at,
            }
            for message in messages
        ],
    )

@router.get("/")
def index() -> RedirectResponse:
    return redirect_to("/admin-ui/login")


@router.get("/login")
def login_page(request: Request):
    if validate_admin_session(request):
        return redirect_to("/admin-ui/licensing")
    message, error, _ = pop_flash(request)
    context = {
        "request": request,
        "title": "Admin Login",
        "message": message,
        "error": error,
        "admin_token_configured": bool(settings.admin_token),
        "is_admin": False,
        "active_context": None,
        "active_page": None,
        "csrf_token": get_csrf_token(request),
    }
    return templates.TemplateResponse("login.html", context)


@router.post("/login")
async def login(request: Request):
    if not settings.admin_token:
        set_flash(request, error="ADMIN_TOKEN is not configured")
        return redirect_to("/admin-ui/login")

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/login")
    if csrf_error:
        return csrf_error

    client_ip = get_client_ip(request)
    if not login_limiter.allow(client_ip):
        set_flash(request, error="Too many login attempts. Try again later.")
        return redirect_to("/admin-ui/login")

    token = str(form.get("admin_token") or "")
    if not hmac.compare_digest(token, settings.admin_token):
        set_flash(request, error="Invalid admin token")
        return redirect_to("/admin-ui/login")

    request.session["is_admin"] = True
    now_ts = int(utcnow().timestamp())
    request.session["login_at"] = now_ts
    request.session["last_seen"] = now_ts
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    set_flash(request, message="Logged in")
    return redirect_to("/admin-ui/licensing")


@router.get("/licensing")
def licensing_dashboard(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    total_tenants = db.query(Tenant).count()
    active_tenants = db.query(Tenant).filter(Tenant.status == TenantStatus.active).count()
    suspended_tenants = db.query(Tenant).filter(Tenant.status == TenantStatus.suspended).count()
    total_licenses = db.query(LicenseKey).count()
    active_licenses = db.query(LicenseKey).filter(LicenseKey.status == LicenseKeyStatus.active).count()
    revoked_licenses = db.query(LicenseKey).filter(LicenseKey.status == LicenseKeyStatus.revoked).count()

    soon_date = utcnow() + timedelta(days=30)
    expiring_soon = (
        db.query(Tenant)
        .filter(Tenant.subscription_expires_at <= soon_date)
        .count()
    )

    context = build_admin_context(
        request,
        "Licensing Dashboard",
        "licensing",
        "licensing-dashboard",
        total_tenants=total_tenants,
        active_tenants=active_tenants,
        suspended_tenants=suspended_tenants,
        total_licenses=total_licenses,
        active_licenses=active_licenses,
        revoked_licenses=revoked_licenses,
        expiring_soon=expiring_soon,
    )
    return templates.TemplateResponse("licensing_dashboard.html", context)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect_to("/admin-ui/login")


@router.get("/tenants")
def list_tenants(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenants = db.query(Tenant).order_by(Tenant.company_code.asc()).all()
    message, error, _ = pop_flash(request)

    context = build_admin_context(
        request,
        "Tenants",
        "licensing",
        "tenants",
        tenants=tenants,
        message=message,
        error=error,
        statuses=[status.value for status in TenantStatus],
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("tenants.html", context)


@router.post("/tenants")
async def create_tenant(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/tenants")
    if csrf_error:
        return csrf_error

    company_code = str(form.get("company_code") or "").strip()
    company_name = str(form.get("company_name") or "").strip()
    erpnext_url = normalize_erpnext_url(str(form.get("erpnext_url") or ""))
    api_key = str(form.get("api_key") or "").strip()
    api_secret = str(form.get("api_secret") or "").strip()
    expires_at_raw = str(form.get("subscription_expires_at") or "").strip()
    status_raw = str(form.get("status") or TenantStatus.active.value).strip()
    is_system = parse_bool_form(form.get("is_system"))

    if not company_code or not erpnext_url or not api_key or not api_secret or not expires_at_raw:
        set_flash(request, error="All fields are required")
        return redirect_to("/admin-ui/tenants")

    if get_tenant_or_none(db, company_code):
        set_flash(request, error="Tenant already exists")
        return redirect_to("/admin-ui/tenants")

    status = parse_tenant_status(status_raw)
    if not status:
        set_flash(request, error="Invalid status")
        return redirect_to("/admin-ui/tenants")

    try:
        expires_at = parse_datetime_input(expires_at_raw)
    except ValueError:
        set_flash(request, error="Invalid date format")
        return redirect_to("/admin-ui/tenants")

    try:
        validated_user = validate_erpnext_credentials(erpnext_url, api_key, api_secret)
    except ERPNextValidationError as exc:
        set_flash(request, error=str(exc))
        return redirect_to("/admin-ui/tenants")

    tenant = Tenant(
        company_code=company_code,
        company_name=company_name or None,
        erpnext_url=erpnext_url,
        api_key=api_key,
        api_secret=api_secret,
        status=status,
        is_system=is_system,
        subscription_expires_at=expires_at,
    )
    db.add(tenant)
    db.commit()

    set_flash(request, message=f"Tenant created. ERP validated as {validated_user}")
    return redirect_to("/admin-ui/tenants")


@router.get("/tenants/{company_code}")
def tenant_detail(request: Request, company_code: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

    request.session["tenant_id"] = str(tenant.id)
    licenses = (
        db.query(LicenseKey)
        .filter(LicenseKey.tenant_id == tenant.id)
        .order_by(LicenseKey.created_at.desc())
        .all()
    )
    message, error, license_key = pop_flash(request)
    tenant_config_snapshot = build_tenant_config_snapshot(tenant)
    tenant_config_payload_json = request.session.pop("flash_tenant_config_payload", None) or json.dumps(
        tenant_config_snapshot.payload.model_dump(mode="python"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    context = build_admin_context(
        request,
        f"Tenant {tenant.company_code}",
        "licensing",
        "tenants",
        tenant=tenant,
        licenses=licenses,
        message=message,
        error=error,
        new_license_key=license_key,
        tenant_config_snapshot=tenant_config_snapshot,
        tenant_config_payload_json=tenant_config_payload_json,
        tenant_statuses=[status.value for status in TenantStatus],
        license_statuses=[status.value for status in LicenseKeyStatus],
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("tenant_detail.html", context)


@router.get("/tenant/channels")
def tenant_channels_page(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response
    tenant = _session_tenant(db, request)
    if not tenant:
        set_flash(request, error="Open tenant details first to select a tenant.")
        return redirect_to("/admin-ui/tenants")
    channel = db.query(TenantChannel).filter(TenantChannel.tenant_id == tenant.id).first()
    tenant_config_snapshot = build_tenant_config_snapshot(tenant)
    ai_config = tenant_config_snapshot.payload.ai.model_dump(mode="python")
    message, error, _ = pop_flash(request)
    context = build_admin_context(
        request,
        f"Channels {tenant.company_code}",
        "licensing",
        "tenant-channels",
        tenant=tenant,
        channel=channel,
        ai_config=ai_config,
        ai_allowed_tools_json=json.dumps(ai_config.get("tools_policy", {}).get("allowed_tools", []), ensure_ascii=False),
        ai_behavior_rules_json=json.dumps(ai_config.get("classification", {}).get("behavior_rules", []), ensure_ascii=False, indent=2),
        ai_intent_rules_json=json.dumps(ai_config.get("classification", {}).get("intent_rules", []), ensure_ascii=False, indent=2),
        ai_prompt_overrides_json=json.dumps(ai_config.get("prompt_overrides", {}), ensure_ascii=False, indent=2),
        message=message,
        error=error,
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("tenant_channels.html", context)


@router.post("/tenant/channels")
async def save_tenant_channels(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response
    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/tenant/channels")
    if csrf_error:
        return csrf_error
    tenant = _session_tenant(db, request)
    if not tenant:
        set_flash(request, error="Open tenant details first to select a tenant.")
        return redirect_to("/admin-ui/tenants")

    tg_bot_token = str(form.get("tg_bot_token") or "").strip() or None
    tg_webhook_secret = str(form.get("tg_webhook_secret") or "").strip() or None
    tg_phone_number = normalize_phone(str(form.get("tg_phone_number") or ""))
    wa_account_sid = str(form.get("wa_account_sid") or "").strip() or None
    wa_auth_token = str(form.get("wa_auth_token") or "").strip() or None
    wa_number = normalize_phone(str(form.get("wa_number") or ""))
    webchat_enabled = parse_bool_form(form.get("webchat_enabled"))
    webchat_widget_token = str(form.get("webchat_widget_token") or "").strip() or None
    webchat_allowed_origins = normalize_json_array(str(form.get("webchat_allowed_origins") or ""))
    ai_system_prompt = str(form.get("ai_system_prompt") or "").strip() or None
    ai_language = str(form.get("ai_language") or "ru").strip() or "ru"
    ai_allow_invoice = parse_bool_form(form.get("ai_allow_invoice"))
    ai_allow_license_ops = parse_bool_form(form.get("ai_allow_license_ops"))
    ai_allow_discount_promises = parse_bool_form(form.get("ai_allow_discount_promises"))
    ai_allow_free_text_catalog_answers = parse_bool_form(form.get("ai_allow_free_text_catalog_answers"))
    ai_handoff_enabled = parse_bool_form(form.get("ai_handoff_enabled"))
    ai_allow_customer_requested_handoff = parse_bool_form(form.get("ai_allow_customer_requested_handoff"))
    ai_frustrated_customer_handoff = parse_bool_form(form.get("ai_frustrated_customer_handoff"))
    ai_handoff_target_type = str(form.get("ai_handoff_target_type") or "none").strip().lower() or "none"
    if ai_handoff_target_type not in {"none", "email", "telegram", "dashboard"}:
        set_flash(request, error="Handoff target type is invalid.")
        return redirect_to("/admin-ui/tenant/channels")
    ai_handoff_target_destination = str(form.get("ai_handoff_target_destination") or "").strip() or None
    ai_handoff_target_instructions = str(form.get("ai_handoff_target_instructions") or "").strip() or None
    try:
        ai_clarification_failure_limit = int(str(form.get("ai_clarification_failure_limit") or "2").strip() or "2")
    except ValueError:
        set_flash(request, error="Clarification failure limit must be an integer.")
        return redirect_to("/admin-ui/tenant/channels")
    if ai_clarification_failure_limit < 1:
        set_flash(request, error="Clarification failure limit must be at least 1.")
        return redirect_to("/admin-ui/tenant/channels")
    ai_allowed_tools_raw = str(form.get("ai_allowed_tools") or "").strip()
    ai_allowed_tools = parse_json_array(ai_allowed_tools_raw)
    if ai_allowed_tools is None:
        set_flash(request, error="Allowed tools must be a JSON array.")
        return redirect_to("/admin-ui/tenant/channels")
    ai_behavior_rules_raw = str(form.get("ai_behavior_rules_json") or "").strip()
    ai_behavior_rules = parse_json_object(ai_behavior_rules_raw)
    if ai_behavior_rules is None or not isinstance(ai_behavior_rules, list):
        set_flash(request, error="Behavior rules must be a JSON array.")
        return redirect_to("/admin-ui/tenant/channels")
    ai_intent_rules_raw = str(form.get("ai_intent_rules_json") or "").strip()
    ai_intent_rules = parse_json_object(ai_intent_rules_raw)
    if ai_intent_rules is None or not isinstance(ai_intent_rules, list):
        set_flash(request, error="Intent rules must be a JSON array.")
        return redirect_to("/admin-ui/tenant/channels")
    ai_prompt_overrides_raw = str(form.get("ai_prompt_overrides_json") or "").strip()
    ai_prompt_overrides = parse_json_object(ai_prompt_overrides_raw)
    if ai_prompt_overrides is None or not isinstance(ai_prompt_overrides, dict):
        set_flash(request, error="Prompt overrides must be a JSON object.")
        return redirect_to("/admin-ui/tenant/channels")

    channel = db.query(TenantChannel).filter(TenantChannel.tenant_id == tenant.id).first()
    if not channel:
        channel = TenantChannel(tenant_id=tenant.id)
        db.add(channel)

    tg_bot_username = channel.tg_bot_username
    if tg_bot_token:
        async with httpx.AsyncClient(timeout=20.0) as client:
            get_me = await client.get(f"https://api.telegram.org/bot{tg_bot_token}/getMe")
            body = get_me.json()
            if get_me.status_code != 200 or not body.get("ok"):
                set_flash(request, error="Telegram bot token is invalid")
                return redirect_to("/admin-ui/tenant/channels")
            tg_bot_username = body.get("result", {}).get("username")
            webhook_base = (settings.ai_agent_webhook_base or "").strip().rstrip("/")
            webhook_url = f"{webhook_base}/webhook/telegram/{tg_bot_token}" if webhook_base else ""
            if webhook_url:
                effective_secret = tg_webhook_secret or channel.tg_webhook_secret or secrets.token_urlsafe(32)
                set_webhook = await client.post(
                    f"https://api.telegram.org/bot{tg_bot_token}/setWebhook",
                    data={
                        "url": webhook_url,
                        "secret_token": effective_secret,
                        "allowed_updates": json.dumps(["message", "callback_query"]),
                    },
                )
                set_webhook_payload = set_webhook.json()
                if set_webhook.status_code != 200 or not set_webhook_payload.get("ok"):
                    detail = set_webhook_payload.get("description", "Failed to set webhook")
                    set_flash(request, error=f"Telegram webhook error: {detail}")
                    return redirect_to("/admin-ui/tenant/channels")
    else:
        tg_bot_username = None

    channel.tg_bot_token = tg_bot_token
    channel.tg_webhook_secret = (tg_webhook_secret or channel.tg_webhook_secret or secrets.token_urlsafe(32)) if tg_bot_token else None
    channel.tg_bot_username = tg_bot_username
    channel.tg_phone_number = tg_phone_number if tg_bot_token else None
    channel.wa_account_sid = wa_account_sid
    channel.wa_auth_token = wa_auth_token
    channel.wa_number = wa_number
    if not wa_account_sid and not wa_auth_token and not wa_number:
        channel.wa_account_sid = None
        channel.wa_auth_token = None
        channel.wa_number = None
    channel.webchat_enabled = webchat_enabled
    channel.webchat_widget_token = webchat_widget_token
    channel.webchat_allowed_origins = webchat_allowed_origins
    channel.ai_system_prompt = ai_system_prompt
    channel.ai_language = ai_language

    tenant_config = _sanitize_tenant_config_payload(_safe_json_dict(tenant.tenant_config))
    ai_config = _safe_json_dict(tenant_config.get("ai"))
    ai_handoff_rules = _safe_json_dict(ai_config.get("handoff_rules"))
    ai_handoff_target = _safe_json_dict(ai_config.get("handoff_target"))
    ai_tools_policy = _safe_json_dict(ai_config.get("tools_policy"))
    ai_classification = _safe_json_dict(ai_config.get("classification"))
    ai_handoff_rules.update(
        {
            "enabled": ai_handoff_enabled,
            "clarification_failure_limit": ai_clarification_failure_limit,
            "allow_customer_requested_handoff": ai_allow_customer_requested_handoff,
            "frustrated_customer_handoff": ai_frustrated_customer_handoff,
        }
    )
    ai_tools_policy.update(
        {
            "allowed_tools": ai_allowed_tools,
        }
    )
    ai_classification.update(
        {
            "behavior_rules": ai_behavior_rules,
            "intent_rules": ai_intent_rules,
        }
    )
    ai_handoff_target.update(
        {
            "target_type": ai_handoff_target_type,
            "destination": ai_handoff_target_destination,
            "instructions": ai_handoff_target_instructions,
        }
    )
    ai_config.update(
        {
            "allow_invoice": ai_allow_invoice,
            "allow_license_ops": ai_allow_license_ops,
            "allow_discount_promises": ai_allow_discount_promises,
            "allow_free_text_catalog_answers": ai_allow_free_text_catalog_answers,
            "handoff_rules": ai_handoff_rules,
            "handoff_target": ai_handoff_target,
            "tools_policy": ai_tools_policy,
            "classification": ai_classification,
            "prompt_overrides": ai_prompt_overrides,
        }
    )
    tenant_config["ai"] = ai_config
    try:
        validated_config = TenantConfigPayload.model_validate(tenant_config)
    except ValidationError as exc:
        details = "; ".join(err.get("msg", "Invalid AI config") for err in exc.errors())
        set_flash(request, error=details or "AI configuration is invalid.")
        return redirect_to("/admin-ui/tenant/channels")
    tenant_config = validated_config.model_dump(mode="python")
    tenant.tenant_config = tenant_config
    tenant.tenant_config_revision = uuid.uuid4().hex
    tenant.tenant_config_updated_at = utcnow()
    tenant.tenant_config_updated_by = "admin-ui"

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        set_flash(request, error="Token or phone number is already attached to another tenant.")
        return redirect_to("/admin-ui/tenant/channels")
    set_flash(request, message="Channels updated")
    return redirect_to("/admin-ui/tenant/channels")


@router.post("/tenant/channels/tg/test")
async def test_telegram_channel(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response
    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/tenant/channels")
    if csrf_error:
        return csrf_error
    tg_bot_token = str(form.get("tg_bot_token") or "").strip()
    if not tg_bot_token:
        set_flash(request, error="Telegram token is required")
        return redirect_to("/admin-ui/tenant/channels")
    tenant = _session_tenant(db, request)
    if not tenant:
        set_flash(request, error="Open tenant details first to select a tenant.")
        return redirect_to("/admin-ui/tenants")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"https://api.telegram.org/bot{tg_bot_token}/getMe")
        payload = resp.json()
        if resp.status_code == 200 and payload.get("ok"):
            username = payload.get("result", {}).get("username", "")
            channel = db.query(TenantChannel).filter(TenantChannel.tenant_id == tenant.id).first()
            if not channel:
                channel = TenantChannel(tenant_id=tenant.id)
                db.add(channel)
            channel.tg_bot_token = tg_bot_token
            channel.tg_bot_username = username or None
            if not channel.tg_webhook_secret:
                channel.tg_webhook_secret = secrets.token_urlsafe(32)
            webhook_base = (settings.ai_agent_webhook_base or "").strip().rstrip("/")
            webhook_url = f"{webhook_base}/webhook/telegram/{tg_bot_token}" if webhook_base else ""
            if webhook_url:
                set_webhook = await client.post(
                    f"https://api.telegram.org/bot{tg_bot_token}/setWebhook",
                    data={
                        "url": webhook_url,
                        "secret_token": channel.tg_webhook_secret,
                        "allowed_updates": json.dumps(["message", "callback_query"]),
                    },
                )
                set_webhook_payload = set_webhook.json()
                if set_webhook.status_code != 200 or not set_webhook_payload.get("ok"):
                    detail = set_webhook_payload.get("description", "Failed to set webhook")
                    db.rollback()
                    set_flash(request, error=f"Telegram webhook error: {detail}")
                    return redirect_to("/admin-ui/tenant/channels")
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                set_flash(request, error="Telegram token is already attached to another tenant.")
                return redirect_to("/admin-ui/tenant/channels")
            set_flash(request, message=f"Telegram OK and saved: @{username}")
        else:
            set_flash(request, error="Telegram token test failed")
    return redirect_to("/admin-ui/tenant/channels")


@router.post("/tenant/channels/wa/test")
async def test_whatsapp_channel(request: Request):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response
    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/tenant/channels")
    if csrf_error:
        return csrf_error
    sid = str(form.get("wa_account_sid") or "").strip()
    token = str(form.get("wa_auth_token") or "").strip()
    if not sid or not token:
        set_flash(request, error="WhatsApp SID and Auth Token are required")
        return redirect_to("/admin-ui/tenant/channels")
    async with httpx.AsyncClient(timeout=15.0, auth=(sid, token)) as client:
        resp = await client.get("https://api.twilio.com/2010-04-01/Accounts.json", params={"PageSize": 1})
        if resp.status_code == 200:
            set_flash(request, message="WhatsApp credentials look valid")
        else:
            set_flash(request, error="WhatsApp credential test failed")
    return redirect_to("/admin-ui/tenant/channels")


@router.get("/licenses")
def list_licenses(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    message, error, _ = pop_flash(request)
    licenses = (
        db.query(LicenseKey)
        .join(Tenant, LicenseKey.tenant_id == Tenant.id)
        .order_by(Tenant.company_code.asc(), LicenseKey.created_at.desc())
        .all()
    )
    license_groups: list[dict[str, object]] = []
    grouped_by_tenant: dict[uuid.UUID, dict[str, object]] = {}
    for license_entry in licenses:
        tenant = license_entry.tenant
        group = grouped_by_tenant.get(tenant.id)
        if group is None:
            group = {"tenant": tenant, "licenses": []}
            grouped_by_tenant[tenant.id] = group
            license_groups.append(group)
        group["licenses"].append(license_entry)

    context = build_admin_context(
        request,
        "Licenses",
        "licensing",
        "licenses",
        license_groups=license_groups,
        message=message,
        error=error,
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("licenses.html", context)


@router.get("/process-jobs")
def process_jobs_page(
    request: Request,
    db: Session = Depends(get_db),
    window_hours: str | None = None,
    status: str | None = None,
    company_code: str | None = None,
    q: str | None = None,
    auto_refresh: str | None = None,
):
    redirect = require_admin_or_redirect(request)
    if redirect:
        return redirect

    selected_window_hours = parse_log_window_hours(window_hours, default=24)
    selected_status = normalize_process_job_status(status)
    selected_company_code = str(company_code or "").strip()
    selected_query = normalize_process_job_query(q)
    selected_auto_refresh = parse_auto_refresh(auto_refresh)
    jobs, summary = list_process_jobs(
        db,
        window_hours=selected_window_hours,
        status=selected_status,
        company_code=selected_company_code or None,
        search_query=selected_query or None,
    )
    message, error, _ = pop_flash(request)
    context = build_admin_context(
        request,
        "Process Jobs",
        "licensing",
        "process-jobs",
        message=message,
        error=error,
        csrf_token=get_csrf_token(request),
        jobs=jobs,
        summary=summary,
        selected_window_hours=selected_window_hours,
        selected_status=selected_status,
        selected_company_code=selected_company_code,
        selected_query=selected_query,
        selected_auto_refresh=selected_auto_refresh,
        default_retention_days=settings.process_job_retention_days,
        default_cleanup_limit=settings.process_job_cleanup_delete_limit,
        stale_after_minutes=settings.process_job_stale_after_minutes,
    )
    return templates.TemplateResponse("process_jobs.html", context)


@router.post("/process-jobs/{job_id}/retry")
async def retry_process_job(
    request: Request,
    job_id: str,
    db: Session = Depends(get_db),
):
    redirect = require_admin_or_redirect(request)
    if redirect:
        return redirect

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/process-jobs")
    if csrf_error:
        return csrf_error

    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        set_flash(request, error="Invalid process job id")
        return redirect_to("/admin-ui/process-jobs")

    job = db.query(ProcessJob).filter(ProcessJob.id == job_uuid).first()
    if not job:
        set_flash(request, error="Process job not found")
        return redirect_to("/admin-ui/process-jobs")
    if job.job_type != "picklist.complete":
        set_flash(request, error="Retry is supported only for Pick List completion jobs")
        return redirect_to("/admin-ui/process-jobs")

    request_meta = job.request_meta if isinstance(job.request_meta, dict) else {}
    new_job = create_process_job(
        db,
        tenant_id=job.tenant_id,
        device_id=job.device_id,
        job_type=job.job_type,
        correlation_id=f"retry-{job.id}",
        request_meta=request_meta,
    )
    notify_process_job_runner()
    set_flash(request, message=f"Retry queued: {new_job.id}")
    return redirect_to("/admin-ui/process-jobs")


@router.post("/process-jobs/{job_id}/requeue")
async def requeue_process_job_page(
    request: Request,
    job_id: str,
    db: Session = Depends(get_db),
):
    redirect = require_admin_or_redirect(request)
    if redirect:
        return redirect

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/process-jobs")
    if csrf_error:
        return csrf_error

    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        set_flash(request, error="Invalid process job id")
        return redirect_to("/admin-ui/process-jobs")

    try:
        job = requeue_process_job(db, job_id=job_uuid)
    except LookupError as exc:
        set_flash(request, error=str(exc))
        return redirect_to("/admin-ui/process-jobs")
    except ValueError as exc:
        set_flash(request, error=str(exc))
        return redirect_to("/admin-ui/process-jobs")

    notify_process_job_runner()
    set_flash(request, message=f"Requeued stale job: {job.id}")
    return redirect_to("/admin-ui/process-jobs")


@router.post("/process-jobs/cleanup")
async def cleanup_process_jobs_page(
    request: Request,
    db: Session = Depends(get_db),
):
    redirect = require_admin_or_redirect(request)
    if redirect:
        return redirect

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/process-jobs")
    if csrf_error:
        return csrf_error

    retention_raw = str(form.get("retention_days") or "").strip()
    limit_raw = str(form.get("limit") or "").strip()
    statuses_raw = form.getlist("statuses")
    dry_run = parse_bool_form(form.get("dry_run"))

    try:
        retention_days = int(retention_raw) if retention_raw else settings.process_job_retention_days
        limit = int(limit_raw) if limit_raw else settings.process_job_cleanup_delete_limit
    except ValueError:
        set_flash(request, error="Cleanup settings must be integers")
        return redirect_to("/admin-ui/process-jobs")

    if retention_days < 1 or limit < 1:
        set_flash(request, error="Cleanup settings must be positive")
        return redirect_to("/admin-ui/process-jobs")

    try:
        statuses = parse_process_job_statuses(statuses_raw)
        result = cleanup_process_jobs(
            db,
            retention_days=retention_days,
            statuses=statuses,
            limit=limit,
            dry_run=dry_run,
        )
    except ValueError as exc:
        set_flash(request, error=str(exc))
        return redirect_to("/admin-ui/process-jobs")
    action_label = "Dry run matched" if dry_run else "Deleted"
    set_flash(
        request,
        message=(
            f"{action_label} {result['matched_count']} process jobs older than "
            f"{result['retention_days']} days."
        ),
    )
    return redirect_to("/admin-ui/process-jobs")


@router.post("/tenants/{company_code}/status")
async def update_tenant_status(request: Request, company_code: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

    form = await request.form()
    csrf_error = require_csrf(request, form, f"/admin-ui/tenants/{company_code}")
    if csrf_error:
        return csrf_error

    status_raw = str(form.get("status") or "").strip()
    status = parse_tenant_status(status_raw)
    if not status:
        set_flash(request, error="Invalid status")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    tenant.status = status
    db.commit()
    set_flash(request, message="Status updated")
    return redirect_to(f"/admin-ui/tenants/{company_code}")


@router.post("/tenants/{company_code}/system")
async def update_tenant_system(request: Request, company_code: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

    form = await request.form()
    csrf_error = require_csrf(request, form, f"/admin-ui/tenants/{company_code}")
    if csrf_error:
        return csrf_error

    tenant.is_system = parse_bool_form(form.get("is_system"))
    db.commit()
    set_flash(request, message="System mode updated")
    return redirect_to(f"/admin-ui/tenants/{company_code}")


@router.post("/tenants/{company_code}/subscription")
async def update_subscription(request: Request, company_code: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

    form = await request.form()
    csrf_error = require_csrf(request, form, f"/admin-ui/tenants/{company_code}")
    if csrf_error:
        return csrf_error

    expires_at_raw = str(form.get("expires_at") or "").strip()
    add_days_raw = str(form.get("add_days") or "").strip()

    if expires_at_raw and add_days_raw:
        set_flash(request, error="Use expires_at or add_days, not both")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    if not expires_at_raw and not add_days_raw:
        set_flash(request, error="Provide expires_at or add_days")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    if expires_at_raw:
        try:
            tenant.subscription_expires_at = parse_datetime_input(expires_at_raw)
        except ValueError:
            set_flash(request, error="Invalid date format")
            return redirect_to(f"/admin-ui/tenants/{company_code}")
    else:
        try:
            add_days = int(add_days_raw)
        except ValueError:
            set_flash(request, error="Invalid add_days")
            return redirect_to(f"/admin-ui/tenants/{company_code}")
        if add_days < 1:
            set_flash(request, error="add_days must be >= 1")
            return redirect_to(f"/admin-ui/tenants/{company_code}")

        now = utcnow()
        base = tenant.subscription_expires_at
        if base < now:
            base = now
        tenant.subscription_expires_at = base + timedelta(days=add_days)

    db.commit()
    set_flash(request, message="Subscription updated")
    return redirect_to(f"/admin-ui/tenants/{company_code}")


@router.post("/tenants/{company_code}/tenant-config")
async def update_tenant_config(request: Request, company_code: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

    form = await request.form()
    csrf_error = require_csrf(request, form, f"/admin-ui/tenants/{company_code}")
    if csrf_error:
        return csrf_error

    current_revision = (tenant.tenant_config_revision or "").strip()
    submitted_revision = str(form.get("config_revision") or "").strip()
    if current_revision and submitted_revision and submitted_revision != current_revision:
        request.session["flash_tenant_config_payload"] = str(form.get("payload_json") or "")
        set_flash(request, error="Tenant config revision mismatch. Reload the page and try again.")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    raw_payload = str(form.get("payload_json") or "").strip()
    if not raw_payload:
        set_flash(request, error="Tenant config payload is required")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    try:
        parsed_payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        request.session["flash_tenant_config_payload"] = raw_payload
        set_flash(request, error=f"Invalid JSON: {exc.msg}")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    if not isinstance(parsed_payload, dict):
        request.session["flash_tenant_config_payload"] = raw_payload
        set_flash(request, error="Tenant config payload must be a JSON object")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    try:
        snapshot = TenantConfigSnapshot(
            config_revision=current_revision,
            settings_scope="tenant",
            payload=parsed_payload,
        )
    except ValidationError as exc:
        request.session["flash_tenant_config_payload"] = raw_payload
        details = "; ".join(err.get("msg", "Invalid config") for err in exc.errors())
        set_flash(request, error=details or "Invalid tenant config payload")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    tenant.tenant_config = _sanitize_tenant_config_payload(snapshot.payload.model_dump(mode="python"))
    tenant.tenant_config_revision = uuid.uuid4().hex
    tenant.tenant_config_updated_by = "admin-ui"
    tenant.tenant_config_updated_at = utcnow()
    db.commit()
    set_flash(request, message="Tenant config updated")
    return redirect_to(f"/admin-ui/tenants/{company_code}")


@router.post("/tenants/{company_code}/delete")
async def delete_tenant(request: Request, company_code: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, f"/admin-ui/tenants/{company_code}")
    if csrf_error:
        return csrf_error

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

    delete_tenant_with_dependencies(db, tenant)
    db.commit()
    set_flash(request, message="Tenant deleted")
    return redirect_to("/admin-ui/tenants")


@router.post("/tenants/{company_code}/licenses")
async def create_license(request: Request, company_code: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenant = get_tenant_or_none(db, company_code)
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/tenants")

    form = await request.form()
    csrf_error = require_csrf(request, form, f"/admin-ui/tenants/{company_code}")
    if csrf_error:
        return csrf_error

    license_key_raw = str(form.get("license_key") or "").strip()
    status_raw = str(form.get("status") or LicenseKeyStatus.active.value).strip()
    description = normalize_license_description(str(form.get("description") or ""))

    status = parse_license_status(status_raw)
    if not status:
        set_flash(request, error="Invalid license status")
        return redirect_to(f"/admin-ui/tenants/{company_code}")

    license_key = license_key_raw or secrets.token_urlsafe(32)
    fingerprint = fingerprint_license_key(license_key) or None
    if fingerprint:
        existing = (
            db.query(LicenseKey)
            .filter(LicenseKey.fingerprint == fingerprint)
            .first()
        )
        if existing:
            set_flash(request, error="License key already exists")
            return redirect_to(f"/admin-ui/tenants/{company_code}")
    license_entry = LicenseKey(
        tenant_id=tenant.id,
        hashed_key=hash_license_key(license_key),
        fingerprint=fingerprint,
        status=status,
        description=description,
    )
    db.add(license_entry)
    db.commit()

    set_flash(request, message="License created", license_key=license_key)
    return redirect_to(f"/admin-ui/tenants/{company_code}")


@router.post("/licenses/{license_id}/status")
async def update_license_status(request: Request, license_id: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    company_code = str(form.get("company_code") or "").strip()
    status_raw = str(form.get("status") or "").strip()
    redirect_target = f"/admin-ui/tenants/{company_code}" if company_code else "/admin-ui/tenants"
    csrf_error = require_csrf(request, form, redirect_target)
    if csrf_error:
        return csrf_error

    status = parse_license_status(status_raw)
    if not status:
        set_flash(request, error="Invalid license status")
        return redirect_to(redirect_target)

    try:
        license_uuid = uuid.UUID(license_id)
    except ValueError:
        set_flash(request, error="Invalid license id")
        return redirect_to(redirect_target)

    license_entry = db.query(LicenseKey).filter(LicenseKey.id == license_uuid).first()
    if not license_entry:
        set_flash(request, error="License not found")
        return redirect_to(redirect_target)

    license_entry.status = status
    db.commit()

    set_flash(request, message="License status updated")
    return redirect_to(redirect_target)


@router.post("/licenses/{license_id}/description")
async def update_license_description(request: Request, license_id: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    company_code = str(form.get("company_code") or "").strip()
    redirect_target = f"/admin-ui/tenants/{company_code}" if company_code else "/admin-ui/licenses"
    csrf_error = require_csrf(request, form, redirect_target)
    if csrf_error:
        return csrf_error

    try:
        license_uuid = uuid.UUID(license_id)
    except ValueError:
        set_flash(request, error="Invalid license id")
        return redirect_to(redirect_target)

    license_entry = db.query(LicenseKey).filter(LicenseKey.id == license_uuid).first()
    if not license_entry:
        set_flash(request, error="License not found")
        return redirect_to(redirect_target)

    description = normalize_license_description(str(form.get("description") or ""))
    license_entry.description = description
    db.commit()

    set_flash(request, message="License description updated")
    return redirect_to(redirect_target)


@router.post("/licenses/{license_id}/delete")
async def delete_license(request: Request, license_id: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    company_code = str(form.get("company_code") or "").strip()
    redirect_target = f"/admin-ui/tenants/{company_code}" if company_code else "/admin-ui/tenants"
    csrf_error = require_csrf(request, form, redirect_target)
    if csrf_error:
        return csrf_error

    try:
        license_uuid = uuid.UUID(license_id)
    except ValueError:
        set_flash(request, error="Invalid license id")
        return redirect_to(redirect_target)

    license_entry = db.query(LicenseKey).filter(LicenseKey.id == license_uuid).first()
    if not license_entry:
        set_flash(request, error="License not found")
        return redirect_to(redirect_target)

    db.delete(license_entry)
    db.commit()
    set_flash(request, message="License deleted")
    return redirect_to(redirect_target)


@router.get("/ota")
def ota_dashboard(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    message, error, _ = pop_flash(request)
    total_firmwares = db.query(Firmware).count()
    active_firmwares = db.query(Firmware).filter(Firmware.is_active == True).count()
    stable_firmwares = db.query(Firmware).filter(Firmware.is_stable == True).count()
    total_logs = db.query(DeviceOTALog).count()
    failed_logs = db.query(DeviceOTALog).filter(DeviceOTALog.status == "failed").count()

    context = build_admin_context(
        request,
        "OTA Dashboard",
        "ota",
        "ota-dashboard",
        total_firmwares=total_firmwares,
        active_firmwares=active_firmwares,
        stable_firmwares=stable_firmwares,
        total_logs=total_logs,
        failed_logs=failed_logs,
        message=message,
        error=error,
    )
    return templates.TemplateResponse("ota_dashboard.html", context)


@router.get("/ota/releases")
def ota_releases(request: Request, db: Session = Depends(get_db), device_type: str | None = None):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    message, error, _ = pop_flash(request)
    query = db.query(Firmware)
    if device_type:
        query = query.filter(Firmware.device_type == device_type)
    firmwares = (
        query.order_by(Firmware.device_type.asc(), Firmware.version.desc(), Firmware.build_number.desc())
        .all()
    )
    device_types = [
        row[0] for row in db.query(Firmware.device_type).distinct().order_by(Firmware.device_type.asc()).all()
    ]

    context = build_admin_context(
        request,
        "OTA Releases",
        "ota",
        "ota-releases",
        firmwares=firmwares,
        device_types=device_types,
        selected_device_type=device_type or "",
        message=message,
        error=error,
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("ota_releases.html", context)


@router.post("/ota/releases")
async def create_ota_release(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/ota/releases")
    if csrf_error:
        return csrf_error

    device_type = str(form.get("device_type") or "").strip()
    version = str(form.get("version") or "").strip()
    build_raw = str(form.get("build_number") or "").strip()
    description = str(form.get("description") or "").strip() or None
    release_notes = str(form.get("release_notes") or "").strip() or None
    min_current_version = str(form.get("min_current_version") or "").strip() or None
    is_stable = bool(form.get("is_stable"))

    upload = form.get("firmware_file")
    if not device_type or not upload:
        set_flash(request, error="Device type and firmware file are required")
        return redirect_to("/admin-ui/ota/releases")

    file_bytes = await upload.read()
    if not file_bytes:
        set_flash(request, error="Uploaded file is empty")
        return redirect_to("/admin-ui/ota/releases")

    parsed_version, parsed_build, raw_version = parse_esp_app_desc_version(file_bytes)
    if parsed_version:
        if version and version != parsed_version:
            set_flash(request, error="Version does not match firmware binary")
            return redirect_to("/admin-ui/ota/releases")
        version = parsed_version
    if parsed_build:
        if build_raw and build_raw != str(parsed_build):
            set_flash(request, error="Build number does not match firmware binary")
            return redirect_to("/admin-ui/ota/releases")
        build_raw = str(parsed_build)

    if not version or not build_raw:
        set_flash(request, error="Version/build missing and could not be read from firmware")
        return redirect_to("/admin-ui/ota/releases")

    try:
        build_number = int(build_raw)
        if build_number < 1:
            raise ValueError
    except ValueError:
        set_flash(request, error="Build number must be a positive integer")
        return redirect_to("/admin-ui/ota/releases")

    if ota_service._parse_version(version) is None:
        set_flash(request, error="Version must be semantic (e.g., 1.2.3)")
        return redirect_to("/admin-ui/ota/releases")

    if min_current_version and ota_service._parse_version(min_current_version) is None:
        set_flash(request, error="Min current version must be semantic (e.g., 1.0.0)")
        return redirect_to("/admin-ui/ota/releases")

    raw_filename = getattr(upload, "filename", None) or f"{device_type}-v{version}-b{build_number}.bin"
    original_filename = str(raw_filename).split("/")[-1].split("\\")[-1]
    safe_device_type = "".join(ch for ch in device_type if ch.isalnum() or ch in ("-", "_")).strip()
    if not safe_device_type:
        set_flash(request, error="Device type contains invalid characters")
        return redirect_to("/admin-ui/ota/releases")

    filename = f"{safe_device_type}-v{version}-b{build_number}.bin"
    existing = (
        db.query(Firmware)
        .filter(
            Firmware.device_type == device_type,
            Firmware.version == version,
            Firmware.build_number == build_number,
        )
        .first()
    )

    binary_path = f"{safe_device_type}/v{version}_b{build_number}.bin"
    file_path = ota_service.firmware_path / binary_path
    file_path.parent.mkdir(parents=True, exist_ok=True)

    file_path.write_bytes(file_bytes)
    file_hash = ota_service.calculate_file_hash(file_path)
    file_size = file_path.stat().st_size

    if existing:
        existing.filename = filename
        existing.file_size = file_size
        existing.file_hash = file_hash
        existing.binary_path = binary_path
        existing.description = description
        existing.release_notes = release_notes
        existing.is_stable = is_stable
        existing.min_current_version = min_current_version
        existing.is_active = True
        if is_stable and existing.released_at is None:
            existing.released_at = utcnow()
        if not is_stable:
            existing.released_at = None
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            set_flash(request, error=f"Firmware upload conflicted with an existing file name. Original upload name: {original_filename}")
            return redirect_to("/admin-ui/ota/releases")
        set_flash(request, message="Firmware release updated: binary and metadata were replaced")
        return redirect_to("/admin-ui/ota/releases")

    firmware = Firmware(
        device_type=device_type,
        version=version,
        build_number=build_number,
        filename=filename,
        file_size=file_size,
        file_hash=file_hash,
        binary_path=binary_path,
        description=description,
        release_notes=release_notes,
        is_stable=is_stable,
        min_current_version=min_current_version,
        is_active=True,
        released_at=utcnow() if is_stable else None,
    )
    db.add(firmware)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        set_flash(request, error=f"Firmware upload conflicted with an existing file name. Original upload name: {original_filename}")
        return redirect_to("/admin-ui/ota/releases")
    set_flash(request, message="Firmware uploaded and registered as a new release")
    return redirect_to("/admin-ui/ota/releases")


@router.post("/ota/inspect", response_class=JSONResponse)
async def inspect_ota_binary(request: Request) -> JSONResponse:
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/ota/releases")
    if csrf_error:
        return JSONResponse({"ok": False, "error": "Invalid CSRF token"}, status_code=403)

    upload = form.get("firmware_file")
    if not upload:
        return JSONResponse({"ok": False, "error": "No file provided"}, status_code=400)

    file_bytes = await upload.read()
    parsed_version, parsed_build, raw_version = parse_esp_app_desc_version(file_bytes)
    if not parsed_version:
        return JSONResponse({"ok": False, "error": "Unable to parse version from firmware"}, status_code=400)

    return JSONResponse(
        {
            "ok": True,
            "version": parsed_version,
            "build_number": parsed_build,
            "raw_version": raw_version,
        }
    )


@router.get("/ota/access")
def ota_access(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    message, error, _ = pop_flash(request)
    current_setting = db.query(OTAAccess).order_by(OTAAccess.id.asc()).first()
    tenants = db.query(Tenant).order_by(Tenant.company_code.asc()).all()
    license_keys = (
        db.query(LicenseKey)
        .join(Tenant, LicenseKey.tenant_id == Tenant.id)
        .order_by(Tenant.company_code.asc(), LicenseKey.created_at.desc())
        .all()
    )

    context = build_admin_context(
        request,
        "ESP32 Access",
        "ota",
        "ota-access",
        tenants=tenants,
        license_keys=license_keys,
        current_setting=current_setting,
        current_tenant_id=current_setting.tenant_id if current_setting else None,
        current_license_id=current_setting.license_key_id if current_setting else None,
        message=message,
        error=error,
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("ota_access.html", context)


@router.post("/ota/access")
async def update_ota_access(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/ota/access")
    if csrf_error:
        return csrf_error

    tenant_raw = str(form.get("tenant_id") or "").strip()
    license_raw = str(form.get("license_key_id") or "").strip()
    if not tenant_raw or not license_raw:
        set_flash(request, error="Select both tenant and license key")
        return redirect_to("/admin-ui/ota/access")

    try:
        tenant_id = uuid.UUID(tenant_raw)
        license_id = uuid.UUID(license_raw)
    except ValueError:
        set_flash(request, error="Invalid tenant or license id")
        return redirect_to("/admin-ui/ota/access")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        set_flash(request, error="Tenant not found")
        return redirect_to("/admin-ui/ota/access")
    if not tenant.is_system:
        set_flash(
            request,
            error="Selected tenant is not marked as system tenant. Enable system mode in tenant settings first.",
        )
        return redirect_to("/admin-ui/ota/access")

    license_key = db.query(LicenseKey).filter(LicenseKey.id == license_id).first()
    if not license_key:
        set_flash(request, error="License key not found")
        return redirect_to("/admin-ui/ota/access")

    if license_key.tenant_id != tenant.id:
        set_flash(request, error="License key does not belong to selected tenant")
        return redirect_to("/admin-ui/ota/access")

    current_setting = db.query(OTAAccess).order_by(OTAAccess.id.asc()).first()
    if current_setting:
        current_setting.tenant_id = tenant.id
        current_setting.license_key_id = license_key.id
    else:
        current_setting = OTAAccess(tenant_id=tenant.id, license_key_id=license_key.id)
        db.add(current_setting)

    db.commit()
    set_flash(request, message="ESP32 access updated")
    return redirect_to("/admin-ui/ota/access")


@router.post("/ota/access/clear")
async def clear_ota_access(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/ota/access")
    if csrf_error:
        return csrf_error

    current_setting = db.query(OTAAccess).order_by(OTAAccess.id.asc()).first()
    if current_setting:
        db.delete(current_setting)
        db.commit()
        set_flash(request, message="ESP32 access cleared")
    return redirect_to("/admin-ui/ota/access")


@router.post("/ota/firmware/{firmware_id}/update")
async def update_ota_release(request: Request, firmware_id: int, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/ota/releases")
    if csrf_error:
        return csrf_error

    firmware = db.query(Firmware).filter(Firmware.id == firmware_id).first()
    if not firmware:
        set_flash(request, error="Firmware not found")
        return redirect_to("/admin-ui/ota/releases")

    min_current_version = str(form.get("min_current_version") or "").strip() or None
    if min_current_version and ota_service._parse_version(min_current_version) is None:
        set_flash(request, error="Min current version must be semantic (e.g., 1.0.0)")
        return redirect_to("/admin-ui/ota/releases")

    firmware.is_stable = bool(form.get("is_stable"))
    firmware.is_active = bool(form.get("is_active"))
    firmware.min_current_version = min_current_version
    if firmware.is_stable and firmware.released_at is None:
        firmware.released_at = utcnow()

    db.commit()
    set_flash(request, message="Firmware updated")
    return redirect_to("/admin-ui/ota/releases")


@router.post("/ota/firmware/{firmware_id}/delete")
async def delete_ota_release(request: Request, firmware_id: int, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/ota/releases")
    if csrf_error:
        return csrf_error

    firmware = db.query(Firmware).filter(Firmware.id == firmware_id).first()
    if not firmware:
        set_flash(request, error="Firmware not found")
        return redirect_to("/admin-ui/ota/releases")

    binary_path = ota_service.get_firmware_binary_path(firmware)
    try:
        if binary_path.exists():
            binary_path.unlink()
    except OSError:
        set_flash(request, error="Firmware file could not be deleted")
        return redirect_to("/admin-ui/ota/releases")

    db.delete(firmware)
    db.commit()
    set_flash(request, message="Firmware deleted")
    return redirect_to("/admin-ui/ota/releases")


@router.get("/ota/devices")
def ota_devices(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    logs = (
        db.query(DeviceOTALog)
        .order_by(DeviceOTALog.updated_at.desc())
        .limit(50)
        .all()
    )

    context = build_admin_context(
        request,
        "OTA Devices",
        "ota",
        "ota-devices",
        logs=logs,
    )
    return templates.TemplateResponse("ota_devices.html", context)


@router.get("/ota/channels")
def ota_channels(request: Request):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    context = build_admin_context(
        request,
        "OTA Channels",
        "ota",
        "ota-channels",
    )
    return templates.TemplateResponse("ota_channels.html", context)


@router.get("/ota/policies")
def ota_policies(request: Request):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    context = build_admin_context(
        request,
        "OTA Policies",
        "ota",
        "ota-policies",
    )
    return templates.TemplateResponse("ota_policies.html", context)


@router.get("/ota/monitoring")
def ota_monitoring(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    failed_logs = (
        db.query(DeviceOTALog)
        .filter(DeviceOTALog.status == "failed")
        .order_by(DeviceOTALog.updated_at.desc())
        .limit(25)
        .all()
    )

    context = build_admin_context(
        request,
        "OTA Monitoring",
        "ota",
        "ota-monitoring",
        failed_logs=failed_logs,
    )
    return templates.TemplateResponse("ota_monitoring.html", context)


@router.get("/erp-allowlist")
def erp_allowlist_page(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    doctypes = (
        db.query(ERPAllowlistEntry)
        .filter(ERPAllowlistEntry.entry_type == ERPAllowlistType.doctype)
        .order_by(ERPAllowlistEntry.value.asc())
        .all()
    )
    methods = (
        db.query(ERPAllowlistEntry)
        .filter(ERPAllowlistEntry.entry_type == ERPAllowlistType.method)
        .order_by(ERPAllowlistEntry.value.asc())
        .all()
    )
    tenants = db.query(Tenant).order_by(Tenant.company_code.asc()).all()

    selected_tenant_id = str(request.query_params.get("tenant_id") or "").strip()
    selected_tenant_uuid: uuid.UUID | None = None
    fetched_doctypes: list[dict[str, object]] = []
    recommended_doctypes = sorted({normalize_doctype(item) for item in MOBILE_APP_DOCTYPES if item})
    recommended_keys = {item.lower() for item in recommended_doctypes}
    allowlist_doctype_keys = {entry.value.lower() for entry in doctypes}

    message, error, _ = pop_flash(request)
    if selected_tenant_id:
        try:
            tenant_uuid = uuid.UUID(selected_tenant_id)
        except ValueError:
            error = error or "Invalid tenant id"
        else:
            selected_tenant_uuid = tenant_uuid
            tenant = db.query(Tenant).filter(Tenant.id == tenant_uuid).first()
            if tenant is None:
                error = error or "Tenant not found"
            else:
                try:
                    fetched_doctypes = fetch_tenant_doctypes(tenant)
                    for row in fetched_doctypes:
                        name = normalize_doctype(str(row.get("name") or ""))
                        key = name.lower()
                        row["recommended"] = key in recommended_keys
                        row["in_allowlist"] = key in allowlist_doctype_keys
                except ERPNextError as exc:
                    error = error or f"ERP request failed: {exc}"
                except HTTPException as exc:
                    error = error or str(exc.detail)
                except Exception as exc:
                    error = error or f"ERP DocType fetch failed: {exc}"

    context = build_admin_context(
        request,
        "ERP Allowlist",
        "licensing",
        "erp-allowlist",
        doctypes=doctypes,
        methods=methods,
        defaults_doctypes=settings.erp_allowed_doctypes,
        defaults_methods=settings.erp_allowed_methods,
        tenants=tenants,
        selected_tenant_id=selected_tenant_id,
        selected_tenant_uuid=selected_tenant_uuid,
        recommended_doctypes=recommended_doctypes,
        fetched_doctypes=fetched_doctypes,
        message=message,
        error=error,
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("erp_allowlist.html", context)


@router.get("/license-diagnostics")
def license_diagnostics_page(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    window_hours = parse_log_window_hours(request.query_params.get("window_hours"))
    scope = normalize_log_scope(request.query_params.get("scope"))
    outcome = normalize_log_outcome(request.query_params.get("outcome"))

    message, error, _ = pop_flash(request)
    diagnostics_logs = fetch_license_event_logs(
        db=db,
        window_hours=window_hours,
        scope=scope,
        outcome=outcome,
    )
    context = build_admin_context(
        request,
        "License Diagnostics",
        "licensing",
        "license-diagnostics",
        diagnostics=None,
        diagnostics_logs=diagnostics_logs,
        selected_window_hours=window_hours,
        selected_scope=scope,
        selected_outcome=outcome,
        form_license_key="",
        form_company_code="",
        message=message,
        error=error,
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("license_diagnostics.html", context)


@router.get("/ai-operations")
def ai_operations_page(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    window_hours = parse_log_window_hours(request.query_params.get("window_hours"))
    selected_view = normalize_ai_operations_view(request.query_params.get("view"))
    scope = normalize_ai_log_scope(request.query_params.get("scope"))
    tenant_code = normalize_free_text_filter(request.query_params.get("tenant")) or None
    channel_type = normalize_ai_channel_type(request.query_params.get("channel")) or None
    session_id = normalize_free_text_filter(request.query_params.get("session")) or None
    event_type = normalize_free_text_filter(request.query_params.get("event_type")) or None
    delivery_status = normalize_delivery_status(request.query_params.get("delivery_status")) or None
    query = normalize_free_text_filter(request.query_params.get("q")) or None
    if selected_view == "failed-deliveries":
        scope = "handoffs"
        delivery_status = "failed"
    elif selected_view == "handoffs":
        scope = "handoffs"
    rows = fetch_ai_audit_logs(
        db=db,
        window_hours=window_hours,
        scope=scope,
        tenant_code=tenant_code,
        channel_type=channel_type,
        session_id=session_id,
        event_type=event_type,
        delivery_status=delivery_status,
        query=query,
    )
    event_type_options = list_ai_audit_event_type_options(
        db=db,
        window_hours=window_hours,
        scope=scope,
        tenant_code=tenant_code,
        channel_type=channel_type,
    )
    handoff_count = sum(1 for row in rows if row["action"] == "ai_handoff")
    event_count = sum(1 for row in rows if row["action"] == "ai_conversation_event")
    delivered_handoff_count = sum(
        1 for row in rows if row["action"] == "ai_handoff" and row.get("delivery_status") == "delivered"
    )
    failed_handoff_count = sum(
        1 for row in rows if row["action"] == "ai_handoff" and row.get("delivery_status") == "failed"
    )

    context = build_admin_context(
        request,
        "AI Operations",
        "licensing",
        "ai-operations",
        rows=rows,
        handoff_count=handoff_count,
        event_count=event_count,
        delivered_handoff_count=delivered_handoff_count,
        failed_handoff_count=failed_handoff_count,
        selected_window_hours=window_hours,
        selected_scope=scope,
        selected_view=selected_view,
        selected_tenant=tenant_code or "",
        selected_channel=channel_type or "",
        selected_session=session_id or "",
        selected_event_type=event_type or "",
        selected_delivery_status=delivery_status or "",
        selected_query=query or "",
        event_type_options=event_type_options,
    )
    return templates.TemplateResponse("ai_operations.html", context)


@router.get("/ai-conversations")
def ai_conversations_page(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    tenant_code = normalize_free_text_filter(request.query_params.get("tenant")) or None
    channel_type = normalize_ai_channel_type(request.query_params.get("channel")) or None
    status = normalize_ai_conversation_status(request.query_params.get("status")) or None
    selected_view = normalize_ai_conversations_view(request.query_params.get("view"))
    last_stage = normalize_ai_stage_filter(request.query_params.get("stage")) or None
    query = normalize_free_text_filter(request.query_params.get("q")) or None
    if selected_view == "needs-attention":
        status = "handoff"
    elif selected_view == "open":
        status = "open"
    elif selected_view == "closed":
        status = "closed"
    rows = list_ai_conversations(
        db,
        tenant_code=tenant_code,
        channel_type=channel_type,
        status=status,
        last_stage=last_stage,
        query=query,
    )
    stage_options = list_ai_conversation_stage_options(
        db,
        tenant_code=tenant_code,
        channel_type=channel_type,
        status=status,
    )
    open_count = sum(1 for row in rows if row["status"] == "open")
    handoff_count = sum(1 for row in rows if row["status"] == "handoff")
    closed_count = sum(1 for row in rows if row["status"] == "closed")

    context = build_admin_context(
        request,
        "AI Conversations",
        "licensing",
        "ai-conversations",
        rows=rows,
        total_count=len(rows),
        open_count=open_count,
        handoff_count=handoff_count,
        closed_count=closed_count,
        stage_options=stage_options,
        selected_view=selected_view,
        selected_tenant=tenant_code or "",
        selected_channel=channel_type or "",
        selected_status=status or "",
        selected_stage=last_stage or "",
        selected_query=query or "",
    )
    return templates.TemplateResponse("ai_conversations.html", context)


@router.get("/ai-conversations/{conversation_id}")
def ai_conversation_detail_page(conversation_id: str, request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    conversation, messages = get_ai_conversation_detail(db, conversation_id)
    if not conversation:
        set_flash(request, error="AI conversation not found.")
        return redirect_to("/admin-ui/ai-conversations")

    operations = fetch_ai_audit_logs(
        db=db,
        window_hours=24 * 7,
        scope="all",
        tenant_code=conversation["tenant_company_code"],
        channel_type=conversation["channel_type"],
        session_id=conversation["session_id"],
        limit=50,
    )
    handoff_count = sum(1 for row in operations if row["action"] == "ai_handoff")
    failed_handoff_count = sum(
        1 for row in operations if row["action"] == "ai_handoff" and row.get("delivery_status") == "failed"
    )
    latest_handoff = next((row for row in operations if row["action"] == "ai_handoff"), None)
    important_events = [
        row
        for row in operations
        if row["action"] == "ai_handoff"
        or row["event_type"] in {"handoff_triggered", "manager_attention_required", "conversation_state_changed"}
    ][:8]

    context = build_admin_context(
        request,
        "AI Conversation",
        "licensing",
        "ai-conversations",
        conversation=conversation,
        messages=messages,
        operations=operations,
        important_events=important_events,
        operation_count=len(operations),
        handoff_count=handoff_count,
        failed_handoff_count=failed_handoff_count,
        latest_handoff=latest_handoff,
    )
    return templates.TemplateResponse("ai_conversation_detail.html", context)


@router.post("/license-diagnostics")
async def run_license_diagnostics(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/license-diagnostics")
    if csrf_error:
        return csrf_error

    window_hours = parse_log_window_hours(str(form.get("window_hours") or "24"))
    scope = normalize_log_scope(str(form.get("scope") or "auth"))
    outcome = normalize_log_outcome(str(form.get("outcome") or "all"))

    raw_license_key = str(form.get("license_key") or "").strip()
    raw_company_code = str(form.get("company_code") or "").strip()
    company_code = raw_company_code or None

    error = None
    diagnostics = None

    try:
        payload = LicenseDiagnosticsRequest(
            license_key=raw_license_key,
            company_code=company_code,
        )
        diagnostics = api_diagnose_license(
            payload=payload,
            request=request,
            db=db,
        )
    except ValidationError as exc:
        details = "; ".join(err.get("msg", "Invalid input") for err in exc.errors())
        error = details or "Invalid diagnostics input."
    except HTTPException as exc:
        error = str(exc.detail or "Diagnostics request failed.")

    diagnostics_logs = fetch_license_event_logs(
        db=db,
        window_hours=window_hours,
        scope=scope,
        outcome=outcome,
    )
    context = build_admin_context(
        request,
        "License Diagnostics",
        "licensing",
        "license-diagnostics",
        diagnostics=diagnostics,
        diagnostics_logs=diagnostics_logs,
        selected_window_hours=window_hours,
        selected_scope=scope,
        selected_outcome=outcome,
        form_license_key=raw_license_key,
        form_company_code=raw_company_code,
        error=error,
        message=None if error else "Diagnostics complete.",
        csrf_token=get_csrf_token(request),
    )
    return templates.TemplateResponse("license_diagnostics.html", context)


@router.post("/erp-allowlist/seed")
async def seed_allowlist(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/erp-allowlist")
    if csrf_error:
        return csrf_error

    inserted = seed_allowlist_from_settings(db)
    if inserted > 0:
        set_flash(request, message=f"Defaults merged into allowlist (+{inserted})")
    elif has_allowlist_entries(db):
        set_flash(request, message="Allowlist already contains all defaults")
    else:
        set_flash(request, error="No defaults configured in .env")
    return redirect_to("/admin-ui/erp-allowlist")


@router.post("/erp-allowlist/seed-mobile")
async def seed_allowlist_mobile(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/erp-allowlist")
    if csrf_error:
        return csrf_error

    inserted = seed_allowlist_mobile_profile(db)
    if inserted > 0:
        set_flash(request, message=f"Mobile app profile merged into allowlist (+{inserted})")
    else:
        set_flash(request, message="Allowlist already contains the mobile app profile")
    return redirect_to("/admin-ui/erp-allowlist")


@router.post("/erp-allowlist/doctypes/bulk")
async def add_allowlist_doctypes_bulk(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/erp-allowlist")
    if csrf_error:
        return csrf_error

    selected_tenant_id = str(form.get("tenant_id") or "").strip()
    selected_values = [normalize_doctype(str(value)) for value in form.getlist("values")]
    selected_values = [value for value in selected_values if value]
    selected_values = list(dict.fromkeys(selected_values))

    if not selected_values:
        set_flash(request, error="Select at least one DocType to add")
        suffix = f"?tenant_id={selected_tenant_id}" if selected_tenant_id else ""
        return redirect_to(f"/admin-ui/erp-allowlist{suffix}")

    if not has_allowlist_entries(db):
        seed_allowlist_from_settings(db)

    existing = (
        db.query(ERPAllowlistEntry)
        .filter(ERPAllowlistEntry.entry_type == ERPAllowlistType.doctype)
        .all()
    )
    existing_names = {entry.value.lower() for entry in existing}

    inserted = 0
    for value in selected_values:
        if value.lower() in existing_names:
            continue
        db.add(ERPAllowlistEntry(entry_type=ERPAllowlistType.doctype, value=value))
        existing_names.add(value.lower())
        inserted += 1

    if inserted > 0:
        try:
            db.commit()
            skipped = len(selected_values) - inserted
            if skipped > 0:
                set_flash(request, message=f"Added {inserted} DocType(s), skipped {skipped} existing")
            else:
                set_flash(request, message=f"Added {inserted} DocType(s)")
        except IntegrityError:
            db.rollback()
            set_flash(request, error="Failed to add some DocTypes (duplicate entries)")
    else:
        set_flash(request, message="Selected DocTypes are already in allowlist")

    suffix = f"?tenant_id={selected_tenant_id}" if selected_tenant_id else ""
    return redirect_to(f"/admin-ui/erp-allowlist{suffix}")


@router.post("/erp-allowlist/doctypes")
async def add_allowlist_doctype(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/erp-allowlist")
    if csrf_error:
        return csrf_error

    raw_value = str(form.get("value") or "")
    value = normalize_doctype(raw_value)
    if not value:
        set_flash(request, error="Doctype is required")
        return redirect_to("/admin-ui/erp-allowlist")

    if not has_allowlist_entries(db):
        seed_allowlist_from_settings(db)

    existing = (
        db.query(ERPAllowlistEntry)
        .filter(ERPAllowlistEntry.entry_type == ERPAllowlistType.doctype)
        .all()
    )
    if any(entry.value.lower() == value.lower() for entry in existing):
        set_flash(request, error="Doctype already exists")
        return redirect_to("/admin-ui/erp-allowlist")

    db.add(ERPAllowlistEntry(entry_type=ERPAllowlistType.doctype, value=value))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        set_flash(request, error="Doctype already exists")
        return redirect_to("/admin-ui/erp-allowlist")

    set_flash(request, message="Doctype added")
    return redirect_to("/admin-ui/erp-allowlist")


@router.post("/erp-allowlist/methods")
async def add_allowlist_method(request: Request, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/erp-allowlist")
    if csrf_error:
        return csrf_error

    raw_value = str(form.get("value") or "")
    value = normalize_method(raw_value)
    if not value:
        set_flash(request, error="Method is required")
        return redirect_to("/admin-ui/erp-allowlist")

    if not has_allowlist_entries(db):
        seed_allowlist_from_settings(db)

    existing = (
        db.query(ERPAllowlistEntry)
        .filter(ERPAllowlistEntry.entry_type == ERPAllowlistType.method)
        .all()
    )
    if any(entry.value.upper() == value for entry in existing):
        set_flash(request, error="Method already exists")
        return redirect_to("/admin-ui/erp-allowlist")

    db.add(ERPAllowlistEntry(entry_type=ERPAllowlistType.method, value=value))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        set_flash(request, error="Method already exists")
        return redirect_to("/admin-ui/erp-allowlist")

    set_flash(request, message="Method added")
    return redirect_to("/admin-ui/erp-allowlist")


@router.post("/erp-allowlist/{entry_id}/delete")
async def delete_allowlist_entry(request: Request, entry_id: str, db: Session = Depends(get_db)):
    redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    form = await request.form()
    csrf_error = require_csrf(request, form, "/admin-ui/erp-allowlist")
    if csrf_error:
        return csrf_error

    try:
        entry_uuid = uuid.UUID(entry_id)
    except ValueError:
        set_flash(request, error="Invalid allowlist entry id")
        return redirect_to("/admin-ui/erp-allowlist")

    entry = db.query(ERPAllowlistEntry).filter(ERPAllowlistEntry.id == entry_uuid).first()
    if not entry:
        set_flash(request, error="Allowlist entry not found")
        return redirect_to("/admin-ui/erp-allowlist")

    db.delete(entry)
    db.commit()
    set_flash(request, message="Allowlist entry deleted")
    return redirect_to("/admin-ui/erp-allowlist")
