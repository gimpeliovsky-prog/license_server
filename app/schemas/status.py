from datetime import datetime

from pydantic import BaseModel


class StatusResponse(BaseModel):
    tenant_status: str
    subscription_active: bool
    grace_active: bool
    subscription_expires_at: datetime | None
    token_expires_at: datetime
    server_time: datetime
