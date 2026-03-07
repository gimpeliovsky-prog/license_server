import json
import logging
from functools import lru_cache
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class ERPNextError(Exception):
    pass


class ERPNextValidationError(ERPNextError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def normalize_erpnext_url(raw: str) -> str:
    trimmed = (raw or "").strip()
    if not trimmed:
        return ""
    if trimmed.lower().startswith(("http://", "https://")):
        return trimmed.rstrip("/")
    return f"https://{trimmed}".rstrip("/")


def request_erpnext(
    base_url: str,
    api_key: str,
    api_secret: str,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    normalized = normalize_erpnext_url(base_url)
    if not normalized:
        raise ERPNextError("ERPNext URL not configured")
    url = f"{normalized}{path}"
    headers = {"Authorization": f"token {api_key}:{api_secret}"}
    try:
        client = get_client()
        response = client.request(method, url, params=params, json=json_body, headers=headers)
        if response.status_code >= 400:
            logger.warning("ERPNext error %s for %s", response.status_code, url)
        return response
    except httpx.RequestError as exc:
        logger.error("ERPNext request failed: %s", exc)
        raise ERPNextError("ERPNext request failed") from exc


def validate_erpnext_credentials(base_url: str, api_key: str, api_secret: str) -> str:
    try:
        response = request_erpnext(
            base_url,
            api_key,
            api_secret,
            "GET",
            "/api/method/frappe.auth.get_logged_user",
        )
    except ERPNextError as exc:
        raise ERPNextValidationError("ERPNext is unreachable or did not respond", status_code=502) from exc

    if response.status_code in {401, 403}:
        raise ERPNextValidationError("ERPNext API key or secret is invalid")
    if response.status_code == 404:
        raise ERPNextValidationError("ERPNext URL is invalid or ERPNext API is unavailable")
    if response.status_code >= 500:
        raise ERPNextValidationError(
            f"ERPNext returned server error during validation ({response.status_code})",
            status_code=502,
        )
    if response.status_code >= 400:
        raise ERPNextValidationError(f"ERPNext rejected validation request ({response.status_code})")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ERPNextValidationError("ERPNext returned invalid JSON during validation", status_code=502) from exc

    if not isinstance(payload, dict):
        raise ERPNextValidationError("ERPNext validation response has unexpected format", status_code=502)

    user = payload.get("message")
    if not isinstance(user, str) or not user.strip():
        raise ERPNextValidationError("ERPNext validation response does not contain user identity", status_code=502)

    normalized_user = user.strip()
    if normalized_user.lower() == "guest":
        raise ERPNextValidationError("ERPNext API key or secret is invalid")

    return normalized_user


def default_fields(fields: list[str]) -> str:
    return json.dumps(fields, separators=(",", ":"))


@lru_cache
def get_client() -> httpx.Client:
    settings = get_settings()
    timeout = httpx.Timeout(settings.erp_timeout_seconds)
    return httpx.Client(timeout=timeout)
