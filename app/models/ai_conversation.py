import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AIConversation(Base):
    __tablename__ = "ai_conversations"
    __table_args__ = (UniqueConstraint("tenant_id", "session_id", name="uq_ai_conversation_session"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    buyer_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("buyer_channel_identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    session_id: Mapped[str] = mapped_column(String(190), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    channel_user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    erp_customer_id: Mapped[str | None] = mapped_column(String(140), nullable=True, index=True)
    buyer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    buyer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    first_customer_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lead_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lead_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    lead_temperature: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    next_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handoff_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sales_owner_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    quality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_flags_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_delivery_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    messages = relationship("AIConversationMessage", back_populates="conversation", cascade="all, delete-orphan")
