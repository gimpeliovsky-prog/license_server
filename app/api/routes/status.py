from fastapi import APIRouter, Depends

from app.api.deps import get_request_context
from app.schemas import StatusResponse
from app.utils.time import utcnow

router = APIRouter(tags=["status"])


@router.get("/status", response_model=StatusResponse)
def status(context=Depends(get_request_context)) -> StatusResponse:
    now = utcnow()
    return StatusResponse(
        tenant_status=context.tenant.status.value,
        subscription_active=context.subscription_active,
        grace_active=context.grace_active,
        subscription_expires_at=context.tenant.subscription_expires_at,
        token_expires_at=context.token.expires_at,
        server_time=now,
    )
