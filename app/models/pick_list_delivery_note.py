import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PickListDeliveryNoteLink(Base):
    __tablename__ = "pick_list_delivery_notes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "pick_list_name", name="uq_pick_list_delivery_note_pick_list"),
        UniqueConstraint("tenant_id", "delivery_note_name", name="uq_pick_list_delivery_note_delivery_note"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    pick_list_name: Mapped[str] = mapped_column(String(140), nullable=False)
    delivery_note_name: Mapped[str] = mapped_column(String(140), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
