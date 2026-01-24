"""Pydantic schemas for OTA firmware endpoints."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class FirmwareBase(BaseModel):
    """Base firmware schema."""

    device_type: str = Field(..., description="Device type (e.g., 'scales_bridge_tab5')")
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$", description="Semantic version (e.g., '1.0.0')")
    build_number: int = Field(..., ge=1)
    description: Optional[str] = None
    release_notes: Optional[str] = None
    is_stable: bool = False
    min_current_version: Optional[str] = None


class FirmwareCreate(FirmwareBase):
    """Schema for creating firmware."""

    filename: str = Field(..., description="Filename of the binary (without path)")
    file_size: int = Field(..., ge=1, description="Size in bytes")
    file_hash: str = Field(..., description="SHA256 hash of the binary")
    binary_path: str = Field(..., description="Path to binary file on server")


class FirmwareUpdate(BaseModel):
    """Schema for updating firmware metadata."""

    description: Optional[str] = None
    release_notes: Optional[str] = None
    is_stable: Optional[bool] = None
    is_active: Optional[bool] = None
    min_current_version: Optional[str] = None


class FirmwareResponse(FirmwareBase):
    """Schema for firmware response."""

    id: int
    filename: str
    file_size: int
    file_hash: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    released_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class FirmwareDetailResponse(FirmwareResponse):
    """Detailed firmware response with additional info."""

    binary_path: str


# OTA Check request/response schemas
class OTACheckRequest(BaseModel):
    """Device checks for firmware update."""

    device_id: int = Field(..., description="Device ID requesting update")
    device_type: str = Field(..., description="Device type")
    current_version: str = Field(..., description="Current firmware version on device")
    current_build: int = Field(..., description="Current build number")


class OTACheckResponse(BaseModel):
    """Response to OTA check."""

    update_available: bool
    firmware_id: Optional[int] = None
    version: Optional[str] = None
    build_number: Optional[int] = None
    description: Optional[str] = None
    download_url: Optional[str] = None  # Signed download URL (may include query params)
    file_hash: Optional[str] = None  # For device to verify integrity
    file_size: Optional[int] = None  # Size in bytes


# OTA Download request/response
class OTADownloadResponse(BaseModel):
    """Response when device downloads firmware binary."""

    firmware_id: int
    filename: str
    file_size: int
    file_hash: str
    # Binary data is streamed separately


# OTA Status update
class OTAStatusUpdate(BaseModel):
    """Device reports OTA status."""

    device_id: int
    firmware_id: int
    status: str = Field(..., description="pending, downloading, installing, success, failed")
    bytes_downloaded: Optional[int] = None
    error_message: Optional[str] = None


class OTALogResponse(BaseModel):
    """OTA log entry response."""

    id: int
    device_id: int
    firmware_id: int
    status: str
    error_message: Optional[str]
    bytes_downloaded: int
    download_started_at: Optional[datetime] = None
    download_completed_at: Optional[datetime] = None
    installed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
