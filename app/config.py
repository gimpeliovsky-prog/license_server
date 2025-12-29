from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="KadimaSoft License Server", alias="APP_NAME")
    database_url: str = Field(alias="DATABASE_URL")
    jwt_secret: str = Field(alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    token_ttl_days: int = Field(default=7, alias="TOKEN_TTL_DAYS")
    grace_days: int = Field(default=7, alias="GRACE_DAYS")
    allow_insecure_http: bool = Field(default=False, alias="ALLOW_INSECURE_HTTP")
    rate_limit_activate_per_minute: int = Field(default=5, alias="RATE_LIMIT_ACTIVATE_PER_MINUTE")
    rate_limit_refresh_per_minute: int = Field(default=10, alias="RATE_LIMIT_REFRESH_PER_MINUTE")
    erp_timeout_seconds: int = Field(default=10, alias="ERP_TIMEOUT_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    admin_token: str | None = Field(default=None, alias="ADMIN_TOKEN")
    session_secret: str | None = Field(default=None, alias="SESSION_SECRET")


@lru_cache
def get_settings() -> Settings:
    return Settings()
