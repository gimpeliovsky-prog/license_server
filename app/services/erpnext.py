import json
import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class ERPNextError(Exception):
    pass


def request_erpnext(
    base_url: str,
    api_key: str,
    api_secret: str,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    settings = get_settings()
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"Authorization": f"token {api_key}:{api_secret}"}
    timeout = httpx.Timeout(settings.erp_timeout_seconds)

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.request(method, url, params=params, json=json_body, headers=headers)
            if response.status_code >= 400:
                logger.warning("ERPNext error %s for %s", response.status_code, url)
            return response
    except httpx.RequestError as exc:
        logger.error("ERPNext request failed: %s", exc)
        raise ERPNextError("ERPNext request failed") from exc


def default_fields(fields: list[str]) -> str:
    return json.dumps(fields, separators=(",", ":"))
