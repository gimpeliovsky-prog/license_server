from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_csv_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                items.append(text)
        return items
    text = str(value).strip()
    return [text] if text else []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="KadimaSoft License Server", alias="APP_NAME")
    database_url: str = Field(alias="DATABASE_URL")
    jwt_secret: str = Field(alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    token_ttl_days: int = Field(default=7, alias="TOKEN_TTL_DAYS")
    grace_days: int = Field(default=7, alias="GRACE_DAYS")
    allow_insecure_http: bool = Field(default=False, alias="ALLOW_INSECURE_HTTP")
    trusted_proxy_nets: list[str] = Field(default_factory=list, alias="TRUSTED_PROXY_NETS")
    rate_limit_activate_per_minute: int = Field(default=5, alias="RATE_LIMIT_ACTIVATE_PER_MINUTE")
    rate_limit_activate_ip_per_minute: int = Field(
        default=30, alias="RATE_LIMIT_ACTIVATE_IP_PER_MINUTE"
    )
    rate_limit_refresh_per_minute: int = Field(default=10, alias="RATE_LIMIT_REFRESH_PER_MINUTE")
    rate_limit_login_per_minute: int = Field(default=10, alias="RATE_LIMIT_LOGIN_PER_MINUTE")
    erp_timeout_seconds: int = Field(default=10, alias="ERP_TIMEOUT_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    admin_token: str | None = Field(default=None, alias="ADMIN_TOKEN")
    session_secret: str | None = Field(default=None, alias="SESSION_SECRET")
    admin_session_max_age_seconds: int = Field(default=8 * 60 * 60, alias="ADMIN_SESSION_MAX_AGE_SECONDS")
    admin_session_idle_seconds: int = Field(default=30 * 60, alias="ADMIN_SESSION_IDLE_SECONDS")
    admin_session_same_site: str = Field(default="lax", alias="ADMIN_SESSION_SAMESITE")
    erp_allowed_doctypes: list[str] = Field(
        default_factory=lambda: [
            "Pick List",
            "Item",
            "Bin",
            "Warehouse",
            "Customer",
            "Purchase Order",
            "Stock Settings",
        ],
        alias="ERP_ALLOWED_DOCTYPES",
    )
    erp_allowed_methods: list[str] = Field(
        default_factory=lambda: ["GET", "POST", "PUT"],
        alias="ERP_ALLOWED_METHODS",
    )

    @field_validator("erp_allowed_doctypes", mode="before")
    @classmethod
    def parse_erp_allowed_doctypes(cls, value: object) -> list[str]:
        return _parse_csv_list(value)

    @field_validator("erp_allowed_methods", mode="before")
    @classmethod
    def parse_erp_allowed_methods(cls, value: object) -> list[str]:
        return [item.upper() for item in _parse_csv_list(value)]

    @field_validator("trusted_proxy_nets", mode="before")
    @classmethod
    def parse_trusted_proxy_nets(cls, value: object) -> list[str]:
        return _parse_csv_list(value)

    @field_validator("admin_session_same_site", mode="before")
    @classmethod
    def parse_admin_session_same_site(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        return text if text in {"lax", "strict", "none"} else "lax"


@lru_cache
def get_settings() -> Settings:
    return Settings()
