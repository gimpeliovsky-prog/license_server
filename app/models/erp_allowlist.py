import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ERPAllowlistType(str, enum.Enum):
    doctype = "doctype"
    method = "method"


class ERPAllowlistEntry(Base):
    __tablename__ = "erp_allowlist"
    __table_args__ = (UniqueConstraint("entry_type", "value", name="uq_erp_allowlist_entry"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_type: Mapped[ERPAllowlistType] = mapped_column(Enum(ERPAllowlistType), nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
