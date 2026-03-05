import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.services.erpnext import normalize_erpnext_url

logger = logging.getLogger(__name__)


class ERPUserAuthError(Exception):
    pass


@dataclass(frozen=True)
class ERPUserIdentity:
    username: str
    roles: list[str]
    full_name: str | None
    enabled: bool


def authenticate_erp_user(base_url: str, username: str, password: str) -> ERPUserIdentity:
    user = username.strip()
    if not user or not password:
        raise ERPUserAuthError("ERP user credentials required")

    normalized = normalize_erpnext_url(base_url)
    if not normalized:
        raise ERPUserAuthError("ERPNext URL not configured")

    settings = get_settings()
    timeout = httpx.Timeout(settings.erp_timeout_seconds)

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            login_response = client.post(
                f"{normalized}/api/method/login",
                data={"usr": user, "pwd": password},
            )
            if login_response.status_code >= 400:
                raise ERPUserAuthError("ERP user credentials invalid")

            logged_user = _fetch_logged_user(client, normalized) or user
            roles = _fetch_roles(client, normalized)
            full_name, enabled = _fetch_user_profile(client, normalized, logged_user)
            if not enabled:
                raise ERPUserAuthError("ERP user disabled")
    except httpx.RequestError as exc:
        logger.error("ERP user auth request failed: %s", exc)
        raise ERPUserAuthError("ERP user auth request failed") from exc

    return ERPUserIdentity(
        username=logged_user,
        roles=roles,
        full_name=full_name,
        enabled=enabled,
    )


def _fetch_logged_user(client: httpx.Client, base_url: str) -> str | None:
    response = client.get(f"{base_url}/api/method/frappe.auth.get_logged_user")
    if response.status_code >= 400:
        return None
    payload = _safe_json(response)
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None


def _fetch_roles(client: httpx.Client, base_url: str) -> list[str]:
    response = client.get(f"{base_url}/api/method/frappe.core.doctype.user.user.get_all_roles")
    if response.status_code >= 400:
        return []
    payload = _safe_json(response)
    message = payload.get("message")
    if not isinstance(message, list):
        return []
    roles: set[str] = set()
    for item in message:
        if isinstance(item, str):
            role = item.strip()
            if role:
                roles.add(role)
    return sorted(roles)


def _fetch_user_profile(client: httpx.Client, base_url: str, username: str) -> tuple[str | None, bool]:
    safe_username = quote(username, safe="")
    response = client.get(
        f"{base_url}/api/resource/User/{safe_username}",
        params={"fields": "[\"name\",\"full_name\",\"enabled\"]"},
    )
    if response.status_code >= 400:
        return None, True

    payload = _safe_json(response)
    data = payload.get("data")
    if not isinstance(data, dict):
        return None, True

    full_name = data.get("full_name")
    normalized_full_name = full_name.strip() if isinstance(full_name, str) and full_name.strip() else None
    return normalized_full_name, _parse_enabled(data.get("enabled"))


def _parse_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return True


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
