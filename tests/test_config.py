from app.config import Settings


def test_default_allowed_doctypes_include_sales_order_translation_and_pricing(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p@localhost:5432/db")
    monkeypatch.setenv("JWT_SECRET", "test-secret")

    settings = Settings()

    expected = {
        "Translation",
        "Sales Order",
        "Item Price",
        "Price List",
        "Pricing Rule",
        "Item Tax Template",
        "Sales Taxes and Charges Template",
    }
    assert expected.issubset(set(settings.erp_allowed_doctypes))
