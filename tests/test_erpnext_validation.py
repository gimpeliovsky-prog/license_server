from unittest.mock import Mock, patch

from app.services.erpnext import ERPNextError, ERPNextValidationError, validate_erpnext_credentials


def _response(status_code: int, payload=None):
    response = Mock()
    response.status_code = status_code
    response.text = ""
    if isinstance(payload, Exception):
        response.json.side_effect = payload
    else:
        response.json.return_value = payload
    return response


def test_validate_erpnext_credentials_accepts_authenticated_user():
    with patch(
        "app.services.erpnext.request_erpnext",
        return_value=_response(200, {"message": "api-user@example.com"}),
    ):
        user = validate_erpnext_credentials("https://erp.example.com", "key", "secret")

    assert user == "api-user@example.com"


def test_validate_erpnext_credentials_rejects_guest_user():
    with patch(
        "app.services.erpnext.request_erpnext",
        return_value=_response(200, {"message": "Guest"}),
    ):
        try:
            validate_erpnext_credentials("https://erp.example.com", "key", "secret")
        except ERPNextValidationError as exc:
            assert str(exc) == "ERPNext API key or secret is invalid"
        else:
            raise AssertionError("Expected ERPNextValidationError")


def test_validate_erpnext_credentials_rejects_invalid_credentials():
    with patch(
        "app.services.erpnext.request_erpnext",
        return_value=_response(403, {"message": "Forbidden"}),
    ):
        try:
            validate_erpnext_credentials("https://erp.example.com", "key", "secret")
        except ERPNextValidationError as exc:
            assert exc.status_code == 400
            assert str(exc) == "ERPNext API key or secret is invalid"
        else:
            raise AssertionError("Expected ERPNextValidationError")


def test_validate_erpnext_credentials_reports_unreachable_erp():
    with patch("app.services.erpnext.request_erpnext", side_effect=ERPNextError("request failed")):
        try:
            validate_erpnext_credentials("https://erp.example.com", "key", "secret")
        except ERPNextValidationError as exc:
            assert exc.status_code == 502
            assert str(exc) == "ERPNext is unreachable or did not respond"
        else:
            raise AssertionError("Expected ERPNextValidationError")
