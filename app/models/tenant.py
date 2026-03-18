import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TenantStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    disabled = "disabled"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    erpnext_url: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key: Mapped[str] = mapped_column(String(255), nullable=False)
    api_secret: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[TenantStatus] = mapped_column(Enum(TenantStatus), nullable=False, default=TenantStatus.active)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    subscription_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tenant_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    tenant_config_revision: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    tenant_config_updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tenant_config_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    license_keys = relationship("LicenseKey", back_populates="tenant", cascade="all, delete-orphan")
    devices = relationship("Device", back_populates="tenant", cascade="all, delete-orphan")
