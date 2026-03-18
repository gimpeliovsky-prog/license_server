from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.deps import RequestContext
from app.api.routes.auth import get_tenant_config, put_tenant_config
from app.models import Tenant, TenantStatus
from app.schemas import TenantConfigSnapshot
from app.services.auth import TokenData


def _build_context(*permissions: str) -> RequestContext:
    tenant = Tenant(
        id=uuid4(),
        company_code="menor",
        company_name="Menor",
        erpnext_url="https://erp.example.com",
        api_key="key",
        api_secret="secret",
        status=TenantStatus.active,
        is_system=False,
        subscription_expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        tenant_config={
            "access": {"client_profile": "STANDARD"},
            "barcodes": [{"id": "rule-1", "name": "Rule 1"}],
            "scales": {"scale_enabled": True, "scale_unit": "kg", "hosts": [{"host": "10.0.0.10"}]},
        },
        tenant_config_revision="rev-1",
        tenant_config_updated_by="admin@example.com",
        tenant_config_updated_at=datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc),
    )
    token = TokenData(
        tenant_id=tenant.id,
        issued_at=datetime(2026, 3, 18, 10, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 3, 25, 10, 0, tzinfo=timezone.utc),
        device_id="device-1",
        erp_username="picker@example.com",
        erp_roles=("Picker",),
        app_permissions=tuple(permissions),
    )
    return RequestContext(
        tenant=tenant,
        device=None,
        token=token,
        erp_username="picker@example.com",
        erp_roles=("Picker",),
        app_permissions=set(permissions),
        subscription_active=True,
        grace_active=False,
    )


def test_get_tenant_config_returns_snapshot():
    context = _build_context()

    snapshot = get_tenant_config(context=context)

    assert snapshot.config_revision == "rev-1"
    assert snapshot.updated_by == "admin@example.com"
    assert snapshot.payload.access.client_profile == "STANDARD"
    assert snapshot.payload.barcodes[0]["id"] == "rule-1"
    assert snapshot.payload.scales.hosts[0].host == "10.0.0.10"


def test_put_tenant_config_allows_legacy_write_without_explicit_permissions():
    context = _build_context()
    db = MagicMock()
    payload = TenantConfigSnapshot(
        config_revision="rev-1",
        payload={
            "access": {"client_profile": "TAMER", "pick_list_images_enabled": True},
            "barcodes": [{"id": "rule-2", "name": "Weight"}],
            "scales": {"scale_enabled": False, "scale_unit": "g", "hosts": []},
        },
    )

    snapshot = put_tenant_config(payload=payload, context=context, db=db)

    assert snapshot.config_revision
    assert snapshot.config_revision != "rev-1"
    assert snapshot.updated_by == "picker@example.com"
    assert snapshot.payload.access.client_profile == "TAMER"
    assert snapshot.payload.scales.scale_unit == "g"
    assert context.tenant.tenant_config["barcodes"][0]["id"] == "rule-2"
    db.add.assert_called_once_with(context.tenant)
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(context.tenant)


def test_put_tenant_config_rejects_read_only_user_when_explicit_permissions_present():
    context = _build_context("tenant_config.read")
    db = MagicMock()
    payload = TenantConfigSnapshot(config_revision="rev-1")

    with pytest.raises(HTTPException) as exc:
        put_tenant_config(payload=payload, context=context, db=db)

    assert exc.value.status_code == 403
    assert "tenant_config.write" in str(exc.value.detail)
    db.commit.assert_not_called()


def test_put_tenant_config_rejects_revision_mismatch():
    context = _build_context("tenant_config.write")
    db = MagicMock()
    payload = TenantConfigSnapshot(config_revision="stale-revision")

    with pytest.raises(HTTPException) as exc:
        put_tenant_config(payload=payload, context=context, db=db)

    assert exc.value.status_code == 409
    assert "revision mismatch" in str(exc.value.detail).lower()
    db.commit.assert_not_called()
