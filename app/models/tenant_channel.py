import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TenantChannel(Base):
    """V1 channel config: one optional config row per tenant, with at most one binding per channel type."""

    __tablename__ = "tenant_channels"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    tg_bot_token: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    tg_bot_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tg_webhook_secret: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tg_phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)

    wa_account_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wa_auth_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wa_number: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True, index=True)

    webchat_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    webchat_widget_token: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    webchat_allowed_origins: Mapped[str | None] = mapped_column(Text, nullable=True)

    ai_system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_language: Mapped[str] = mapped_column(String(8), nullable=False, default="ru")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="channel")
