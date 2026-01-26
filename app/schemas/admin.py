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


class TenantResponse(BaseModel):
    id: UUID
    company_code: str
    company_name: str | None = None
    erpnext_url: str
    status: str
    subscription_expires_at: datetime


class TenantStatusUpdateRequest(BaseModel):
    status: str


class SubscriptionUpdateRequest(BaseModel):
    expires_at: datetime | None = None
    add_days: int | None = Field(default=None, ge=1, le=3650)


class LicenseCreateRequest(BaseModel):
    company_code: str = Field(..., min_length=1, max_length=64)
    status: str = "active"
    license_key: str | None = None


class LicenseResponse(BaseModel):
    id: UUID
    status: str
    created_at: datetime
    license_key: str | None = None


class LicenseStatusUpdateRequest(BaseModel):
    status: str


class DeviceResponse(BaseModel):
    device_id: str
    revoked: bool
    last_seen: datetime | None


class DeviceRevokeRequest(BaseModel):
    revoked: bool
