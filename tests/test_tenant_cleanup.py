from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock

from app.models import Device, LicenseKey
from app.services.tenant_cleanup import delete_tenant_with_dependencies


def test_delete_tenant_with_dependencies_removes_related_rows_before_tenant():
    tenant = SimpleNamespace(id=uuid4())
    device_id = uuid4()
    license_id = uuid4()

    device_query = MagicMock()
    device_query.filter.return_value.all.return_value = [SimpleNamespace(id=device_id)]

    license_query = MagicMock()
    license_query.filter.return_value.all.return_value = [SimpleNamespace(id=license_id)]

    ota_access_query = MagicMock()
    erp_user_query = MagicMock()
    pairing_token_query = MagicMock()
    idempotency_query = MagicMock()
    audit_query = MagicMock()

    db = MagicMock()
    db.query.side_effect = [
        device_query,
        license_query,
        ota_access_query,
        erp_user_query,
        pairing_token_query,
        idempotency_query,
        audit_query,
    ]

    delete_tenant_with_dependencies(db, tenant)

    assert db.query.call_args_list[0].args[0] is Device.id
    assert db.query.call_args_list[1].args[0] is LicenseKey.id
    ota_access_query.filter.return_value.delete.assert_called_once_with(synchronize_session=False)
    erp_user_query.filter.return_value.delete.assert_called_once_with(synchronize_session=False)
    pairing_token_query.filter.return_value.delete.assert_called_once_with(synchronize_session=False)
    idempotency_query.filter.return_value.delete.assert_called_once_with(synchronize_session=False)
    audit_query.filter.return_value.delete.assert_called_once_with(synchronize_session=False)
    db.delete.assert_called_once_with(tenant)
