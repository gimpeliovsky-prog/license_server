from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ServerCapabilitiesResponse(BaseModel):
    process_api_version: int = 1
    supports_picklist_process: bool = True
    supports_picklist_async_completion: bool = True
    supports_delivery_note_creation: bool = True
    supports_box_count_custom_fields: bool = True


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


class PairingRegisterRequest(BaseModel):
    subdomain: str = Field(..., min_length=1, max_length=63)
    erp_username: str = Field(..., min_length=1, max_length=128)
    erp_password: str = Field(..., min_length=1, max_length=256)


class PairingRegisterResponse(BaseModel):
    pairing_token: str
    expires_at: datetime
    server_time: datetime
    erp_url: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    issued_at: datetime
    expires_at: datetime
    server_time: datetime
    erp_url: str | None = None


class CurrentUserResponse(BaseModel):
    tenant_id: UUID
    company_code: str
    erp_username: str
    full_name: str | None = None
    enabled: bool = True
    erp_roles: list[str] = Field(default_factory=list)
    app_permissions: list[str] = Field(default_factory=list)
    capabilities: ServerCapabilitiesResponse = Field(default_factory=ServerCapabilitiesResponse)
