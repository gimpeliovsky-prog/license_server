from datetime import datetime

from app.services.crm_identity_sync import normalize_phone, parse_erp_datetime


def test_normalize_phone_normalizes_plus_and_digits():
    assert normalize_phone("055-770-4571") == "+0557704571"
    assert normalize_phone("+972 55 770 4571") == "+972557704571"
    assert normalize_phone("123") is None


def test_parse_erp_datetime_supports_iso_and_erp_formats():
    iso_value = parse_erp_datetime("2026-04-09T00:00:00+00:00")
    erp_value = parse_erp_datetime("2026-04-09 00:00:00.123456")

    assert isinstance(iso_value, datetime)
    assert isinstance(erp_value, datetime)
    assert parse_erp_datetime("") is None
