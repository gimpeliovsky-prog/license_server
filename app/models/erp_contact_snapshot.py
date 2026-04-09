import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ERPContactSnapshot(Base):
    __tablename__ = "erp_contact_snapshots"
    __table_args__ = (UniqueConstraint("tenant_id", "erp_contact_id", name="uq_erp_contact_snapshot"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    erp_contact_id: Mapped[str] = mapped_column(String(140), nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(140), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(140), nullable=True)
    email_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mobile_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalized_phone: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    normalized_mobile_no: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    modified_at_erp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
