from __future__ import annotations

from urllib.parse import quote

from fastapi import HTTPException

from app.services.erpnext import request_tenant_erpnext


def normalize_media_path(file_path: str) -> str:
    normalized = (file_path or "").strip().lstrip("/")
    if not normalized:
        raise HTTPException(status_code=400, detail="File path is required")
    return normalized


def fetch_public_file(tenant, file_path: str):
    safe_path = quote(normalize_media_path(file_path), safe="/")
    return request_tenant_erpnext(tenant, "GET", f"/files/{safe_path}")


def fetch_private_file(tenant, file_path: str):
    safe_path = quote(normalize_media_path(file_path), safe="/")
    return request_tenant_erpnext(tenant, "GET", f"/private/files/{safe_path}")
