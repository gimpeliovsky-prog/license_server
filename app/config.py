from functools import lru_cache
import json

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


def parse_proxy_net_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                return _parse_csv_list(json.loads(text))
            except json.JSONDecodeError:
                pass
        return _parse_csv_list(text)
    return _parse_csv_list(value)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="KadimaSoft License Server", alias="APP_NAME")
    app_version: str = Field(default="", alias="APP_VERSION")
    database_url: str = Field(alias="DATABASE_URL")
    jwt_secret: str = Field(alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    token_ttl_days: int = Field(default=7, alias="TOKEN_TTL_DAYS")
    grace_days: int = Field(default=7, alias="GRACE_DAYS")
    allow_insecure_http: bool = Field(default=False, alias="ALLOW_INSECURE_HTTP")
    trusted_proxy_nets: str = Field(default="", alias="TRUSTED_PROXY_NETS")
    rate_limit_activate_per_minute: int = Field(default=5, alias="RATE_LIMIT_ACTIVATE_PER_MINUTE")
    rate_limit_activate_ip_per_minute: int = Field(
        default=30, alias="RATE_LIMIT_ACTIVATE_IP_PER_MINUTE"
    )
    rate_limit_refresh_per_minute: int = Field(default=10, alias="RATE_LIMIT_REFRESH_PER_MINUTE")
    rate_limit_login_per_minute: int = Field(default=10, alias="RATE_LIMIT_LOGIN_PER_MINUTE")
    erp_timeout_seconds: int = Field(default=10, alias="ERP_TIMEOUT_SECONDS")
    company_registry_timeout_seconds: int = Field(default=10, alias="COMPANY_REGISTRY_TIMEOUT_SECONDS")
    pairing_domain_suffix: str = Field(default="kadimasoft.com", alias="PAIRING_DOMAIN_SUFFIX")
    pairing_token_ttl_minutes: int = Field(default=10, alias="PAIRING_TOKEN_TTL_MINUTES")
    pairing_qr_scheme: str = Field(default="tsd://pair", alias="PAIRING_QR_SCHEME")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    admin_token: str | None = Field(default=None, alias="ADMIN_TOKEN")
    ai_agent_token: str = Field(default="", alias="AI_AGENT_TOKEN")
    ai_agent_webhook_base: str = Field(default="", alias="AI_AGENT_WEBHOOK_BASE")
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from_email: str = Field(default="", alias="SMTP_FROM_EMAIL")
    smtp_use_tls: bool = Field(default=True, alias="SMTP_USE_TLS")
    smtp_use_ssl: bool = Field(default=False, alias="SMTP_USE_SSL")
    ai_handoff_email_subject_prefix: str = Field(default="[AI Handoff]", alias="AI_HANDOFF_EMAIL_SUBJECT_PREFIX")
    session_secret: str | None = Field(default=None, alias="SESSION_SECRET")
    admin_session_max_age_seconds: int = Field(default=8 * 60 * 60, alias="ADMIN_SESSION_MAX_AGE_SECONDS")
    admin_session_idle_seconds: int = Field(default=30 * 60, alias="ADMIN_SESSION_IDLE_SECONDS")
    admin_session_same_site: str = Field(default="lax", alias="ADMIN_SESSION_SAMESITE")
    ota_download_secret: str | None = Field(default=None, alias="OTA_DOWNLOAD_SECRET")
    ota_download_ttl_seconds: int = Field(default=10 * 60, alias="OTA_DOWNLOAD_TTL_SECONDS")
    process_api_enabled: bool = Field(default=True, alias="PROCESS_API_ENABLED")
    async_picklist_completion_enabled: bool = Field(default=True, alias="ASYNC_PICKLIST_COMPLETION_ENABLED")
    delivery_note_creation_enabled: bool = Field(default=True, alias="DELIVERY_NOTE_CREATION_ENABLED")
    box_count_custom_fields_enabled: bool = Field(default=True, alias="BOX_COUNT_CUSTOM_FIELDS_ENABLED")
    process_api_disabled_tenants: list[str] = Field(default_factory=list, alias="PROCESS_API_DISABLED_TENANTS")
    async_picklist_completion_disabled_tenants: list[str] = Field(
        default_factory=list, alias="ASYNC_PICKLIST_COMPLETION_DISABLED_TENANTS"
    )
    delivery_note_creation_disabled_tenants: list[str] = Field(
        default_factory=list, alias="DELIVERY_NOTE_CREATION_DISABLED_TENANTS"
    )
    box_count_custom_fields_disabled_tenants: list[str] = Field(
        default_factory=list, alias="BOX_COUNT_CUSTOM_FIELDS_DISABLED_TENANTS"
    )
    process_job_runner_enabled: bool = Field(default=True, alias="PROCESS_JOB_RUNNER_ENABLED")
    process_job_runner_poll_seconds: int = Field(default=5, alias="PROCESS_JOB_RUNNER_POLL_SECONDS")
    process_job_stale_after_minutes: int = Field(default=5, alias="PROCESS_JOB_STALE_AFTER_MINUTES")
    process_job_retention_days: int = Field(default=30, alias="PROCESS_JOB_RETENTION_DAYS")
    process_job_cleanup_delete_limit: int = Field(default=1000, alias="PROCESS_JOB_CLEANUP_DELETE_LIMIT")
    process_job_failed_alert_threshold: int = Field(default=1, alias="PROCESS_JOB_FAILED_ALERT_THRESHOLD")
    process_job_stale_alert_threshold: int = Field(default=1, alias="PROCESS_JOB_STALE_ALERT_THRESHOLD")
    crm_identity_sync_enabled: bool = Field(default=True, alias="CRM_IDENTITY_SYNC_ENABLED")
    crm_identity_sync_timezone: str = Field(default="Asia/Jerusalem", alias="CRM_IDENTITY_SYNC_TIMEZONE")
    crm_identity_sync_hour: int = Field(default=0, alias="CRM_IDENTITY_SYNC_HOUR")
    crm_identity_sync_minute: int = Field(default=0, alias="CRM_IDENTITY_SYNC_MINUTE")
    crm_identity_sync_page_size: int = Field(default=200, alias="CRM_IDENTITY_SYNC_PAGE_SIZE")
    erp_allowed_doctypes: list[str] = Field(
        default_factory=lambda: [
            "Pick List",
            "Item",
            "Translation",
            "Bin",
            "Warehouse",
            "Customer",
            "Purchase Order",
            "Sales Order",
            "Item Price",
            "Price List",
            "Pricing Rule",
            "Item Tax Template",
            "Sales Taxes and Charges Template",
            "Stock Settings",
            "Item Barcode",
        ],
        alias="ERP_ALLOWED_DOCTYPES",
    )
    erp_allowed_methods: list[str] = Field(
        default_factory=lambda: ["GET", "POST", "PUT", "DELETE"],
        alias="ERP_ALLOWED_METHODS",
    )

    @field_validator("erp_allowed_doctypes", mode="before")
    @classmethod
    def parse_erp_allowed_doctypes(cls, value: object) -> list[str]:
        return _parse_csv_list(value)

    @field_validator(
        "process_api_disabled_tenants",
        "async_picklist_completion_disabled_tenants",
        "delivery_note_creation_disabled_tenants",
        "box_count_custom_fields_disabled_tenants",
        mode="before",
    )
    @classmethod
    def parse_tenant_lists(cls, value: object) -> list[str]:
        return [item.lower() for item in _parse_csv_list(value)]

    @field_validator("erp_allowed_methods", mode="before")
    @classmethod
    def parse_erp_allowed_methods(cls, value: object) -> list[str]:
        return [item.upper() for item in _parse_csv_list(value)]

    @field_validator("admin_session_same_site", mode="before")
    @classmethod
    def parse_admin_session_same_site(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        return text if text in {"lax", "strict", "none"} else "lax"

    @field_validator("crm_identity_sync_hour", mode="before")
    @classmethod
    def parse_crm_sync_hour(cls, value: object) -> int:
        try:
            hour = int(value)
        except (TypeError, ValueError):
            return 0
        return hour if 0 <= hour <= 23 else 0

    @field_validator("crm_identity_sync_minute", mode="before")
    @classmethod
    def parse_crm_sync_minute(cls, value: object) -> int:
        try:
            minute = int(value)
        except (TypeError, ValueError):
            return 0
        return minute if 0 <= minute <= 59 else 0

    @property
    def trusted_proxy_net_list(self) -> list[str]:
        return parse_proxy_net_list(self.trusted_proxy_nets)


@lru_cache
def get_settings() -> Settings:
    return Settings()
