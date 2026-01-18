"""OTA (Over-The-Air) update API routes."""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user
from app.models.firmware import Firmware
from app.schemas.auth import UserSchema
from app.schemas.ota import (
    FirmwareCreate,
    FirmwareResponse,
    FirmwareDetailResponse,
    FirmwareUpdate,
    OTACheckRequest,
    OTACheckResponse,
    OTAStatusUpdate,
    OTALogResponse,
)
from app.services.ota import OTAService

router = APIRouter(prefix="/ota", tags=["ota"])
ota_service = OTAService(firmware_base_path="firmware")

logger = logging.getLogger(__name__)


# ============================================================================
# Device-facing OTA endpoints (public, require device auth)
# ============================================================================


@router.post("/check", response_model=OTACheckResponse)
async def check_firmware_update(
    request: OTACheckRequest,
    db: Session = Depends(get_db),
) -> OTACheckResponse:
    """Check if firmware update is available for a device.
    
    This endpoint is called by ESP32 devices to check for available updates.
    """
    try:
        return ota_service.check_update_available(db, request)
    except Exception as e:
        logger.error(f"Error checking firmware update: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error checking firmware update",
        )


@router.get("/download/{firmware_id}")
async def download_firmware(
    firmware_id: int,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Download firmware binary.
    
    Device downloads the binary file for flashing.
    Returns the .bin file with proper headers for OTA.
    """
    firmware = ota_service.get_firmware_for_download(db, firmware_id)
    if not firmware:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firmware not found or inactive",
        )

    binary_path = ota_service.get_firmware_binary_path(firmware)
    if not binary_path.exists():
        logger.error(f"Firmware file not found: {binary_path}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firmware file not found on server",
        )

    return FileResponse(
        path=binary_path,
        filename=firmware.filename,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename={firmware.filename}",
            "X-Firmware-Version": firmware.version,
            "X-Firmware-Build": str(firmware.build_number),
            "X-Firmware-Hash": firmware.file_hash,
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.post("/status")
async def update_ota_status(
    status_update: OTAStatusUpdate,
    db: Session = Depends(get_db),
) -> dict:
    """Device reports OTA operation status.
    
    Called by device during and after firmware update process.
    Statuses: pending, downloading, installing, success, failed
    """
    try:
        # Find existing log or create new one
        from app.models.firmware import DeviceOTALog

        log = (
            db.query(DeviceOTALog)
            .filter(
                DeviceOTALog.device_id == status_update.device_id,
                DeviceOTALog.firmware_id == status_update.firmware_id,
            )
            .order_by(DeviceOTALog.created_at.desc())
            .first()
        )

        if not log:
            log = ota_service.create_ota_log(
                db, status_update.device_id, status_update.firmware_id
            )

        ota_service.update_ota_status(db, log.id, status_update)

        return {
            "success": True,
            "log_id": log.id,
            "status": status_update.status,
        }
    except Exception as e:
        logger.error(f"Error updating OTA status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating status",
        )


# ============================================================================
# Admin endpoints for managing firmware (require authentication)
# ============================================================================


@router.post("/admin/firmware", response_model=FirmwareDetailResponse)
async def create_firmware(
    firmware_create: FirmwareCreate,
    current_user: UserSchema = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FirmwareDetailResponse:
    """Create a new firmware record (admin only).
    
    This registers a new firmware version in the database.
    The binary file should be uploaded separately or pre-placed on disk.
    """
    # Check if firmware version already exists
    existing = (
        db.query(Firmware)
        .filter(
            Firmware.device_type == firmware_create.device_type,
            Firmware.version == firmware_create.version,
            Firmware.build_number == firmware_create.build_number,
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Firmware version already exists",
        )

    # Verify binary file exists
    binary_path = ota_service.firmware_path / firmware_create.binary_path.lstrip("/")
    if not binary_path.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Binary file not found on server",
        )

    # Verify file hash
    calculated_hash = ota_service.calculate_file_hash(binary_path)
    if calculated_hash.lower() != firmware_create.file_hash.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File hash mismatch",
        )

    firmware = Firmware(
        device_type=firmware_create.device_type,
        version=firmware_create.version,
        build_number=firmware_create.build_number,
        filename=firmware_create.filename,
        file_size=firmware_create.file_size,
        file_hash=firmware_create.file_hash,
        binary_path=firmware_create.binary_path,
        description=firmware_create.description,
        release_notes=firmware_create.release_notes,
        is_stable=firmware_create.is_stable,
        min_current_version=firmware_create.min_current_version,
    )

    db.add(firmware)
    db.commit()
    db.refresh(firmware)

    logger.info(
        f"Created firmware {firmware.device_type} v{firmware.version} "
        f"(build {firmware.build_number})"
    )

    return FirmwareDetailResponse.from_orm(firmware)


@router.post("/admin/upload")
async def upload_firmware_binary(
    file: UploadFile = File(...),
    device_type: str = None,
    version: str = None,
    current_user: UserSchema = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Upload a firmware binary file.
    
    Stores the binary file on disk and returns path and hash.
    Must be called before creating firmware record.
    """
    try:
        if not device_type or not version:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="device_type and version are required",
            )

        # Create device-specific directory
        device_dir = ota_service.firmware_path / device_type
        device_dir.mkdir(parents=True, exist_ok=True)

        # Save file
        file_path = device_dir / f"v{version}.bin"
        content = await file.read()

        with open(file_path, "wb") as f:
            f.write(content)

        # Calculate hash
        file_hash = ota_service.calculate_file_hash(file_path)

        return {
            "success": True,
            "filename": file.filename,
            "device_type": device_type,
            "version": version,
            "binary_path": f"{device_type}/v{version}.bin",
            "file_size": len(content),
            "file_hash": file_hash,
        }
    except Exception as e:
        logger.error(f"Error uploading firmware: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error uploading firmware",
        )


@router.get("/admin/firmware", response_model=list[FirmwareResponse])
async def list_firmware(
    device_type: str = None,
    skip: int = 0,
    limit: int = 100,
    current_user: UserSchema = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[FirmwareResponse]:
    """List all firmware versions."""
    query = db.query(Firmware)

    if device_type:
        query = query.filter(Firmware.device_type == device_type)

    firmware_list = query.order_by(
        Firmware.device_type, Firmware.version.desc()
    ).offset(skip).limit(limit).all()

    return [FirmwareResponse.from_orm(f) for f in firmware_list]


@router.get("/admin/firmware/{firmware_id}", response_model=FirmwareDetailResponse)
async def get_firmware(
    firmware_id: int,
    current_user: UserSchema = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FirmwareDetailResponse:
    """Get firmware details by ID."""
    firmware = db.query(Firmware).filter(Firmware.id == firmware_id).first()
    if not firmware:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firmware not found",
        )

    return FirmwareDetailResponse.from_orm(firmware)


@router.patch("/admin/firmware/{firmware_id}", response_model=FirmwareDetailResponse)
async def update_firmware(
    firmware_id: int,
    firmware_update: FirmwareUpdate,
    current_user: UserSchema = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FirmwareDetailResponse:
    """Update firmware metadata."""
    firmware = db.query(Firmware).filter(Firmware.id == firmware_id).first()
    if not firmware:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firmware not found",
        )

    update_data = firmware_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(firmware, field, value)

    db.commit()
    db.refresh(firmware)

    return FirmwareDetailResponse.from_orm(firmware)


@router.delete("/admin/firmware/{firmware_id}")
async def delete_firmware(
    firmware_id: int,
    current_user: UserSchema = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Delete firmware (sets is_active=False instead of hard delete)."""
    firmware = db.query(Firmware).filter(Firmware.id == firmware_id).first()
    if not firmware:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firmware not found",
        )

    firmware.is_active = False
    db.commit()

    return {"success": True, "message": "Firmware deactivated"}


@router.get("/admin/logs", response_model=list[OTALogResponse])
async def get_ota_logs(
    device_id: int = None,
    firmware_id: int = None,
    status: str = None,
    skip: int = 0,
    limit: int = 100,
    current_user: UserSchema = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[OTALogResponse]:
    """Get OTA operation logs."""
    from app.models.firmware import DeviceOTALog

    query = db.query(DeviceOTALog)

    if device_id:
        query = query.filter(DeviceOTALog.device_id == device_id)
    if firmware_id:
        query = query.filter(DeviceOTALog.firmware_id == firmware_id)
    if status:
        query = query.filter(DeviceOTALog.status == status)

    logs = query.order_by(DeviceOTALog.created_at.desc()).offset(skip).limit(limit).all()

    return [OTALogResponse.from_orm(log) for log in logs]
