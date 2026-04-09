from app.config import Settings


def test_crm_identity_sync_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p@localhost:5432/db")
    monkeypatch.setenv("JWT_SECRET", "test-secret")

    settings = Settings()

    assert settings.crm_identity_sync_enabled is True
    assert settings.crm_identity_sync_timezone == "Asia/Jerusalem"
    assert settings.crm_identity_sync_hour == 0
    assert settings.crm_identity_sync_minute == 0
