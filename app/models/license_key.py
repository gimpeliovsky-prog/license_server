import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class LicenseKeyStatus(str, enum.Enum):
    active = "active"
    revoked = "revoked"


class LicenseKey(Base):
    __tablename__ = "license_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hashed_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    status: Mapped[LicenseKeyStatus] = mapped_column(
        Enum(LicenseKeyStatus), nullable=False, default=LicenseKeyStatus.active
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant = relationship("Tenant", back_populates="license_keys")
