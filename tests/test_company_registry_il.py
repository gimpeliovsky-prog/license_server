from app.services.company_registry_il import extract_company_number, normalize_company_name


def test_extract_company_number_from_hebrew_text():
    assert extract_company_number('ח.פ 513690867') == "513690867"
    assert extract_company_number("מספר חברה 510000045") == "510000045"
    assert extract_company_number("abc") is None


def test_normalize_company_name_collapses_variants():
    assert normalize_company_name('צעד קדימה חוויות ותוכן בע~מ') == normalize_company_name('צעד קדימה חוויות ותוכן בעמ')
