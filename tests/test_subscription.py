from datetime import datetime, timedelta, timezone

from app.services.subscription import evaluate_subscription


def test_subscription_active():
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    expires_at = now + timedelta(days=1)

    state = evaluate_subscription(expires_at, issued_at=now, now=now, grace_days=7)

    assert state.subscription_active is True
    assert state.grace_active is False
    assert state.allowed is True


def test_subscription_grace_active():
    now = datetime(2025, 1, 10, tzinfo=timezone.utc)
    expires_at = now - timedelta(days=1)
    issued_at = expires_at - timedelta(days=1)

    state = evaluate_subscription(expires_at, issued_at=issued_at, now=now, grace_days=7)

    assert state.subscription_active is False
    assert state.grace_active is True
    assert state.allowed is True


def test_subscription_grace_denied_after_deadline():
    now = datetime(2025, 1, 20, tzinfo=timezone.utc)
    expires_at = now - timedelta(days=8)
    issued_at = expires_at - timedelta(days=1)

    state = evaluate_subscription(expires_at, issued_at=issued_at, now=now, grace_days=7)

    assert state.subscription_active is False
    assert state.grace_active is False
    assert state.allowed is False


def test_subscription_grace_denied_when_issued_late():
    now = datetime(2025, 1, 2, tzinfo=timezone.utc)
    expires_at = now - timedelta(days=1)
    issued_at = now

    state = evaluate_subscription(expires_at, issued_at=issued_at, now=now, grace_days=7)

    assert state.subscription_active is False
    assert state.grace_active is False
    assert state.allowed is False
