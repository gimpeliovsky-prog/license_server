from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ActivateRequest(BaseModel):
    license_key: str = Field(..., min_length=8, max_length=256)
    device_id: str = Field(..., min_length=1, max_length=128)
    company_code: str | None = Field(default=None, min_length=1, max_length=64)
    erp_username: str | None = Field(default=None, min_length=1, max_length=128)
    erp_password: str | None = Field(default=None, min_length=1, max_length=256)


class LicenseValidateRequest(BaseModel):
    license_key: str = Field(..., min_length=8, max_length=256)
    company_code: str | None = Field(default=None, min_length=1, max_length=64)


class LicenseValidateResponse(BaseModel):
    valid: bool = True
    tenant_id: UUID
    company_code: str
    server_time: datetime


class PairingActivateRequest(BaseModel):
    pairing_token: str = Field(..., min_length=16, max_length=512)
    device_id: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    issued_at: datetime
    expires_at: datetime
    server_time: datetime


class CurrentUserResponse(BaseModel):
    tenant_id: UUID
    company_code: str
    erp_username: str
    full_name: str | None = None
    enabled: bool = True
    erp_roles: list[str] = Field(default_factory=list)
    app_permissions: list[str] = Field(default_factory=list)
