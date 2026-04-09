from app.services.phone_numbers import local_phone_variant, normalize_phone, phone_match_variants


def test_israeli_local_number_normalizes_to_e164():
    assert normalize_phone("0557704571") == "+972557704571"
    assert normalize_phone("+972557704571") == "+972557704571"


def test_israeli_e164_exposes_local_variant():
    assert local_phone_variant("+972557704571") == "0557704571"


def test_phone_match_variants_include_e164_and_local():
    variants = phone_match_variants("0557704571")
    assert "+972557704571" in variants
    assert "0557704571" in variants
