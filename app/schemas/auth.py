from datetime import datetime

from pydantic import BaseModel, Field


class ActivateRequest(BaseModel):
    license_key: str = Field(..., min_length=8, max_length=256)
    device_id: str = Field(..., min_length=1, max_length=128)
    company_code: str | None = Field(default=None, min_length=1, max_length=64)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    issued_at: datetime
    expires_at: datetime
    server_time: datetime
