import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ERPContactCustomerLink(Base):
    __tablename__ = "erp_contact_customer_links"
    __table_args__ = (UniqueConstraint("tenant_id", "erp_contact_id", "erp_customer_id", name="uq_erp_contact_customer_link"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    erp_contact_id: Mapped[str] = mapped_column(String(140), nullable=False, index=True)
    erp_customer_id: Mapped[str] = mapped_column(String(140), nullable=False, index=True)
    modified_at_erp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
