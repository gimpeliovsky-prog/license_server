from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TenantCreateRequest(BaseModel):
    company_code: str = Field(..., min_length=1, max_length=64)
    company_name: str | None = Field(default=None, max_length=255)
    erpnext_url: str = Field(..., min_length=1, max_length=255)
    api_key: str = Field(..., min_length=1, max_length=255)
    api_secret: str = Field(..., min_length=1, max_length=255)
    subscription_expires_at: datetime
    status: str = "active"
    is_system: bool = False


class TenantResponse(BaseModel):
    id: UUID
    company_code: str
    company_name: str | None = None
    erpnext_url: str
    status: str
    is_system: bool = False
    subscription_expires_at: datetime


class TenantCredentialsUpdateRequest(BaseModel):
    erpnext_url: str | None = Field(default=None, min_length=1, max_length=255)
    api_key: str | None = Field(default=None, min_length=1, max_length=255)
    api_secret: str | None = Field(default=None, min_length=1, max_length=255)


class TenantStatusUpdateRequest(BaseModel):
    status: str


class TenantSystemUpdateRequest(BaseModel):
    is_system: bool


class SubscriptionUpdateRequest(BaseModel):
    expires_at: datetime | None = None
    add_days: int | None = Field(default=None, ge=1, le=3650)


class LicenseCreateRequest(BaseModel):
    company_code: str = Field(..., min_length=1, max_length=64)
    status: str = "active"
    license_key: str | None = None
    description: str | None = Field(default=None, max_length=512)


class LicenseResponse(BaseModel):
    id: UUID
    status: str
    created_at: datetime
    license_key: str | None = None
    description: str | None = None


class LicenseStatusUpdateRequest(BaseModel):
    status: str


class DeviceResponse(BaseModel):
    device_id: str
    revoked: bool
    last_seen: datetime | None


class DeviceRevokeRequest(BaseModel):
    revoked: bool


class LicenseDiagnosticsRequest(BaseModel):
    license_key: str = Field(..., min_length=8, max_length=256)
    company_code: str | None = Field(default=None, min_length=1, max_length=64)


class LicenseDiagnosticsTenantInfo(BaseModel):
    id: UUID | None = None
    company_code: str | None = None
    status: str | None = None
    subscription_expires_at: datetime | None = None
    subscription_active: bool | None = None


class LicenseDiagnosticsLicenseInfo(BaseModel):
    id: UUID | None = None
    status: str | None = None
    tenant_id: UUID | None = None
    tenant_company_code: str | None = None
    created_at: datetime | None = None
    fingerprint: str | None = None


class LicenseDiagnosticsResponse(BaseModel):
    valid: bool
    reason_code: str
    reason_detail: str
    server_time: datetime
    input_company_code: str | None = None
    normalized_company_code: str | None = None
    key_fingerprint: str | None = None
    tenant: LicenseDiagnosticsTenantInfo = Field(default_factory=LicenseDiagnosticsTenantInfo)
    matched_license: LicenseDiagnosticsLicenseInfo | None = None
