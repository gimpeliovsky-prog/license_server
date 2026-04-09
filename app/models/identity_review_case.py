import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IdentityReviewCase(Base):
    __tablename__ = "identity_review_cases"

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
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    channel_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_query: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_registry_number: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    official_company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    matched_erp_customer_id: Mapped[str | None] = mapped_column(String(140), nullable=True, index=True)
    matched_erp_customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open", index=True)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    requires_operator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
