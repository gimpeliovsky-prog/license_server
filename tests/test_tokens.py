from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.services.auth import TokenExpired, TokenInvalid, create_access_token, decode_access_token


def test_create_and_decode_token():
    issued_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    tenant_id = uuid4()
    token, data = create_access_token(
        tenant_id,
        device_id="device-1",
        issued_at=issued_at,
        ttl_days=7,
        secret="test-secret",
        algorithm="HS256",
    )

    decoded = decode_access_token(token, secret="test-secret", algorithm="HS256")

    assert decoded.tenant_id == tenant_id
    assert decoded.device_id == "device-1"
    assert decoded.issued_at == issued_at
    assert decoded.expires_at == issued_at + timedelta(days=7)


def test_decode_expired_token():
    issued_at = datetime.now(timezone.utc) - timedelta(days=8)
    token, _ = create_access_token(
        uuid4(),
        issued_at=issued_at,
        ttl_days=7,
        secret="test-secret",
        algorithm="HS256",
    )

    with pytest.raises(TokenExpired):
        decode_access_token(token, secret="test-secret", algorithm="HS256")


def test_decode_invalid_token():
    token, _ = create_access_token(
        uuid4(),
        issued_at=datetime.now(timezone.utc),
        ttl_days=7,
        secret="test-secret",
        algorithm="HS256",
    )

    with pytest.raises(TokenInvalid):
        decode_access_token(token, secret="wrong-secret", algorithm="HS256")
