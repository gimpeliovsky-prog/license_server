"""OTA Firmware model for device updates."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, LargeBinary, DateTime, Boolean, Text
from sqlalchemy.orm import relationship
from app.db.base import Base


class Firmware(Base):
    """Firmware update model for ESP32 devices."""

    __tablename__ = "firmware"

    id = Column(Integer, primary_key=True, index=True)
    device_type = Column(String(50), nullable=False, index=True)  # e.g., "scales_bridge_tab5"
    version = Column(String(20), nullable=False)  # e.g., "1.0.0"
    build_number = Column(Integer, nullable=False)  # Build counter
    
    # File info
    filename = Column(String(255), nullable=False, unique=True)
    file_size = Column(Integer, nullable=False)  # Bytes
    file_hash = Column(String(64), nullable=False)  # SHA256
    
    # Binary data - stored as file path reference
    # For large files, better to store path and serve from disk
    binary_path = Column(String(500), nullable=False)  # /firmware/scales_bridge/v1.0.0.bin
    
    # Metadata
    description = Column(Text, nullable=True)
    release_notes = Column(Text, nullable=True)
    
    # Release control
    is_stable = Column(Boolean, default=False)  # Is this a stable release
    is_active = Column(Boolean, default=True)   # Can devices download this version
    min_current_version = Column(String(20), nullable=True)  # Minimum version required to OTA from
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    released_at = Column(DateTime, nullable=True)
    
    # Relations
    device_ota_logs = relationship("DeviceOTALog", back_populates="firmware", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Firmware {self.device_type} v{self.version} (build {self.build_number})>"


class DeviceOTALog(Base):
    """Log of OTA attempts and updates for devices."""

    __tablename__ = "device_ota_log"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, nullable=False, index=True)  # Reference to Device model
    firmware_id = Column(Integer, nullable=False, index=True)  # Which firmware version
    
    # Status tracking
    status = Column(String(20), nullable=False)  # pending, downloading, installing, success, failed
    error_message = Column(Text, nullable=True)
    
    # Progress tracking
    bytes_downloaded = Column(Integer, default=0)
    download_started_at = Column(DateTime, nullable=True)
    download_completed_at = Column(DateTime, nullable=True)
    
    # Result
    installed_at = Column(DateTime, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relations
    firmware = relationship("Firmware", back_populates="device_ota_logs")

    def __repr__(self) -> str:
        return f"<DeviceOTALog device_id={self.device_id} firmware_id={self.firmware_id} status={self.status}>"
