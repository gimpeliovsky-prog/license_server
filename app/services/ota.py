"""OTA (Over-The-Air) update service."""
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models.firmware import Firmware, DeviceOTALog
from app.schemas.ota import OTACheckRequest, OTACheckResponse, OTAStatusUpdate

logger = logging.getLogger(__name__)


class OTAService:
    """Service for managing OTA updates."""

    def __init__(self, firmware_base_path: str = "firmware"):
        """Initialize OTA service.
        
        Args:
            firmware_base_path: Base directory path for storing firmware files
        """
        self.firmware_path = Path(firmware_base_path)
        self.firmware_path.mkdir(parents=True, exist_ok=True)

    def check_update_available(
        self,
        db: Session,
        request: OTACheckRequest,
    ) -> OTACheckResponse:
        """Check if an update is available for the device.
        
        Args:
            db: Database session
            request: OTA check request with device info
            
        Returns:
            OTACheckResponse with update details if available
        """
        # Find latest stable firmware for this device type
        firmwares = (
            db.query(Firmware)
            .filter(
                Firmware.device_type == request.device_type,
                Firmware.is_active == True,
                Firmware.is_stable == True,
            )
            .all()
        )

        latest_firmware = None
        latest_version = None
        for firmware in firmwares:
            parsed = self._parse_version(firmware.version)
            if parsed is None:
                continue
            if latest_firmware is None or (parsed, firmware.build_number) > (
                latest_version,
                latest_firmware.build_number,
            ):
                latest_firmware = firmware
                latest_version = parsed

        if not latest_firmware:
            return OTACheckResponse(update_available=False)

        # Check if update is needed (version comparison)
        current_version = self._parse_version(request.current_version)
        if current_version is None:
            logger.warning(f"Invalid current version format: {request.current_version}")
            return OTACheckResponse(update_available=False)

        is_newer_version = latest_version > current_version
        is_newer_build = latest_version == current_version and (
            latest_firmware.build_number > request.current_build
        )

        if is_newer_version or is_newer_build:
            # Check minimum version requirement
            if (
                latest_firmware.min_current_version
                and not self._is_version_gte(
                    request.current_version, latest_firmware.min_current_version
                )
            ):
                logger.warning(
                    f"Device {request.device_id} version {request.current_version} "
                    f"is too old. Minimum required: {latest_firmware.min_current_version}"
                )
                return OTACheckResponse(update_available=False)

            return OTACheckResponse(
                update_available=True,
                firmware_id=latest_firmware.id,
                version=latest_firmware.version,
                build_number=latest_firmware.build_number,
                description=latest_firmware.description,
                download_url=f"/api/ota/download/{latest_firmware.id}",
                file_hash=latest_firmware.file_hash,
                file_size=latest_firmware.file_size,
            )

        return OTACheckResponse(update_available=False)

    def get_firmware_for_download(self, db: Session, firmware_id: int) -> Optional[Firmware]:
        """Get firmware by ID for download.
        
        Args:
            db: Database session
            firmware_id: Firmware ID
            
        Returns:
            Firmware object or None
        """
        return (
            db.query(Firmware)
            .filter(Firmware.id == firmware_id, Firmware.is_active == True)
            .first()
        )

    def get_firmware_binary_path(self, firmware: Firmware) -> Path:
        """Get full path to firmware binary file.
        
        Args:
            firmware: Firmware object
            
        Returns:
            Path to the binary file
        """
        return self.firmware_path / firmware.binary_path.lstrip("/")

    def firmware_binary_exists(self, firmware: Firmware) -> bool:
        """Check if firmware binary file exists.
        
        Args:
            firmware: Firmware object
            
        Returns:
            True if file exists
        """
        return self.get_firmware_binary_path(firmware).exists()

    def verify_firmware_hash(self, firmware: Firmware, file_data: bytes) -> bool:
        """Verify firmware binary against stored hash.
        
        Args:
            firmware: Firmware object
            file_data: Binary data to verify
            
        Returns:
            True if hash matches
        """
        calculated_hash = hashlib.sha256(file_data).hexdigest()
        return calculated_hash.lower() == firmware.file_hash.lower()

    def create_ota_log(
        self,
        db: Session,
        device_id: int,
        firmware_id: int,
        status: str = "pending",
    ) -> DeviceOTALog:
        """Create OTA log entry.
        
        Args:
            db: Database session
            device_id: Device ID
            firmware_id: Firmware ID
            status: Initial status
            
        Returns:
            Created OTA log entry
        """
        log = DeviceOTALog(
            device_id=device_id,
            firmware_id=firmware_id,
            status=status,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    def update_ota_status(
        self,
        db: Session,
        log_id: int,
        status_update: OTAStatusUpdate,
    ) -> Optional[DeviceOTALog]:
        """Update OTA log status.
        
        Args:
            db: Database session
            log_id: OTA log ID
            status_update: Status update data
            
        Returns:
            Updated OTA log or None
        """
        log = db.query(DeviceOTALog).filter(DeviceOTALog.id == log_id).first()
        if not log:
            return None

        log.status = status_update.status
        if status_update.bytes_downloaded is not None:
            log.bytes_downloaded = status_update.bytes_downloaded

        if status_update.error_message:
            log.error_message = status_update.error_message

        # Update timestamps based on status
        if status_update.status == "downloading":
            if not log.download_started_at:
                log.download_started_at = datetime.utcnow()
        elif status_update.status == "installing":
            if not log.download_completed_at:
                log.download_completed_at = datetime.utcnow()
        elif status_update.status == "success":
            log.installed_at = datetime.utcnow()

        db.commit()
        db.refresh(log)
        return log

    @staticmethod
    def _is_newer_version(new_version: str, current_version: str) -> bool:
        """Compare semantic versions. Returns True if new_version > current_version.
        
        Args:
            new_version: New version string (e.g., "1.2.3")
            current_version: Current version string
            
        Returns:
            True if new version is newer
        """
        try:
            new_parts = tuple(map(int, new_version.split(".")))
            current_parts = tuple(map(int, current_version.split(".")))
            return new_parts > current_parts
        except (ValueError, AttributeError):
            logger.warning(f"Invalid version format: {new_version} or {current_version}")
            return False

    @staticmethod
    def _parse_version(version: str) -> Optional[tuple[int, int, int]]:
        """Parse semantic version to tuple.

        Args:
            version: Version string (e.g., "1.2.3")

        Returns:
            Tuple of ints or None if invalid
        """
        try:
            parts = tuple(map(int, version.split(".")))
            if len(parts) != 3:
                return None
            return parts
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _is_version_gte(version: str, min_version: str) -> bool:
        """Check if version >= min_version.
        
        Args:
            version: Version to check
            min_version: Minimum required version
            
        Returns:
            True if version >= min_version
        """
        try:
            v_parts = tuple(map(int, version.split(".")))
            min_parts = tuple(map(int, min_version.split(".")))
            return v_parts >= min_parts
        except (ValueError, AttributeError):
            logger.warning(f"Invalid version format: {version} or {min_version}")
            return False

    def calculate_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            Hex digest of SHA256 hash
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
