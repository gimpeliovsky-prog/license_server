from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

from app.config import get_settings
from app.utils.time import utcnow


class TokenError(Exception):
    pass


class TokenExpired(TokenError):
    pass


class TokenInvalid(TokenError):
    pass


@dataclass(frozen=True)
class TokenData:
    tenant_id: UUID
    issued_at: datetime
    expires_at: datetime
    device_id: str | None = None
    erp_username: str | None = None
    erp_roles: tuple[str, ...] = ()
    app_permissions: tuple[str, ...] = ()


def create_access_token(
    tenant_id: UUID,
    device_id: str | None = None,
    erp_username: str | None = None,
    erp_roles: list[str] | tuple[str, ...] | None = None,
    app_permissions: list[str] | tuple[str, ...] | None = None,
    issued_at: datetime | None = None,
    ttl_days: int | None = None,
    secret: str | None = None,
    algorithm: str | None = None,
) -> tuple[str, TokenData]:
    settings = None
    issued_at = issued_at or utcnow()
    if ttl_days is None or secret is None or algorithm is None:
        settings = get_settings()
    ttl_days = ttl_days if ttl_days is not None else settings.token_ttl_days
    expires_at = issued_at + timedelta(days=ttl_days)

    payload = {
        "tenant_id": str(tenant_id),
        "device_id": device_id,
        "erp_username": erp_username,
        "erp_roles": list(erp_roles or []),
        "app_permissions": list(app_permissions or []),
        "issued_at": int(issued_at.timestamp()),
        "expires_at": int(expires_at.timestamp()),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    token = jwt.encode(
        payload,
        secret or settings.jwt_secret,
        algorithm=algorithm or settings.jwt_algorithm,
    )
    return token, TokenData(
        tenant_id=tenant_id,
        issued_at=issued_at,
        expires_at=expires_at,
        device_id=device_id,
        erp_username=erp_username,
        erp_roles=tuple(erp_roles or ()),
        app_permissions=tuple(app_permissions or ()),
    )


def decode_access_token(
    token: str,
    secret: str | None = None,
    algorithm: str | None = None,
) -> TokenData:
    settings = None
    if secret is None or algorithm is None:
        settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            secret or settings.jwt_secret,
            algorithms=[algorithm or settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenInvalid("Token invalid") from exc

    tenant_id = payload.get("tenant_id")
    issued_at = payload.get("issued_at")
    expires_at = payload.get("expires_at")
    device_id = payload.get("device_id")
    erp_username = payload.get("erp_username")
    raw_erp_roles = payload.get("erp_roles")
    raw_app_permissions = payload.get("app_permissions")

    if not tenant_id or not issued_at or not expires_at:
        raise TokenInvalid("Token payload missing required claims")
    if erp_username is not None and not isinstance(erp_username, str):
        raise TokenInvalid("Token payload has invalid erp_username claim")

    erp_roles: tuple[str, ...] = ()
    if raw_erp_roles is not None:
        if not isinstance(raw_erp_roles, list):
            raise TokenInvalid("Token payload has invalid erp_roles claim")
        cleaned: list[str] = []
        for role in raw_erp_roles:
            if not isinstance(role, str):
                raise TokenInvalid("Token payload has invalid erp_roles claim")
            text = role.strip()
            if text:
                cleaned.append(text)
        erp_roles = tuple(cleaned)

    app_permissions: tuple[str, ...] = ()
    if raw_app_permissions is not None:
        if not isinstance(raw_app_permissions, list):
            raise TokenInvalid("Token payload has invalid app_permissions claim")
        cleaned_permissions: list[str] = []
        for permission in raw_app_permissions:
            if not isinstance(permission, str):
                raise TokenInvalid("Token payload has invalid app_permissions claim")
            text = permission.strip()
            if text:
                cleaned_permissions.append(text)
        app_permissions = tuple(cleaned_permissions)

    return TokenData(
        tenant_id=UUID(tenant_id),
        issued_at=datetime.fromtimestamp(issued_at, tz=timezone.utc),
        expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
        device_id=device_id,
        erp_username=erp_username,
        erp_roles=erp_roles,
        app_permissions=app_permissions,
    )
