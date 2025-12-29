from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import get_settings
from app.utils.time import utcnow


@dataclass(frozen=True)
class SubscriptionState:
    subscription_active: bool
    grace_active: bool
    allowed: bool


def evaluate_subscription(
    subscription_expires_at: datetime,
    issued_at: datetime,
    now: datetime | None = None,
    grace_days: int | None = None,
) -> SubscriptionState:
    now = now or utcnow()
    if grace_days is None:
        grace_days = get_settings().grace_days

    if now <= subscription_expires_at:
        return SubscriptionState(subscription_active=True, grace_active=False, allowed=True)

    grace_deadline = subscription_expires_at + timedelta(days=grace_days)
    grace_active = now <= grace_deadline and issued_at <= subscription_expires_at
    return SubscriptionState(subscription_active=False, grace_active=grace_active, allowed=grace_active)
