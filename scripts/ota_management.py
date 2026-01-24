#!/usr/bin/env python3
"""
OTA Management Script
Управление микропрограммами и обновлениями для ESP32 устройств
"""

import argparse
import sys
import requests
import json
import hashlib
from pathlib import Path
from typing import Optional
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OTAManager:
    """Manager for OTA operations."""

    def __init__(self, server_url: str, admin_token: str):
        """Initialize OTA manager.
        
        Args:
            server_url: Base URL of OTA server
            admin_token: Admin token (X-Admin-Token)
        """
        self.server_url = server_url.rstrip('/')
        self.admin_token = admin_token
        self.headers = {
            'X-Admin-Token': admin_token,
            'Content-Type': 'application/json'
        }

    def upload_firmware(
        self,
        binary_path: str,
        device_type: str,
        version: str,
    ) -> dict:
        """Upload firmware binary file.
        
        Args:
            binary_path: Path to .bin file
            device_type: Device type (e.g., 'scales_bridge_tab5')
            version: Version string (e.g., '1.0.0')
            
        Returns:
            Upload info including hash and file size
        """
        file_path = Path(binary_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {binary_path}")

        logger.info(f"Uploading {file_path.name}...")

        with open(file_path, 'rb') as f:
            files = {'file': (file_path.name, f)}
            data = {
                'device_type': device_type,
                'version': version,
            }
            
            response = requests.post(
                f"{self.server_url}/api/ota/admin/upload",
                files=files,
                data=data,
                headers={'X-Admin-Token': self.admin_token}  # No JSON header for multipart
            )

        if response.status_code != 200:
            raise Exception(f"Upload failed: {response.text}")

        result = response.json()
        logger.info(f"✓ Uploaded successfully")
        logger.info(f"  Binary path: {result['binary_path']}")
        logger.info(f"  File size: {result['file_size']} bytes")
        logger.info(f"  SHA256: {result['file_hash']}")
        
        return result

    def register_firmware(
        self,
        device_type: str,
        version: str,
        build_number: int,
        filename: str,
        file_size: int,
        file_hash: str,
        binary_path: str,
        description: Optional[str] = None,
        release_notes: Optional[str] = None,
        is_stable: bool = False,
        min_current_version: Optional[str] = None,
    ) -> dict:
        """Register firmware in database.
        
        Args:
            device_type: Device type
            version: Version string
            build_number: Build number
            filename: Filename
            file_size: File size in bytes
            file_hash: SHA256 hash
            binary_path: Path to binary relative to firmware directory
            description: Short description
            release_notes: Detailed release notes
            is_stable: Is this a stable release
            min_current_version: Minimum version to upgrade from
            
        Returns:
            Registered firmware info
        """
        payload = {
            'device_type': device_type,
            'version': version,
            'build_number': build_number,
            'filename': filename,
            'file_size': file_size,
            'file_hash': file_hash,
            'binary_path': binary_path,
            'description': description,
            'release_notes': release_notes,
            'is_stable': is_stable,
            'min_current_version': min_current_version,
        }

        logger.info(f"Registering firmware {device_type} v{version}...")

        response = requests.post(
            f"{self.server_url}/api/ota/admin/firmware",
            json=payload,
            headers=self.headers
        )

        if response.status_code != 200:
            raise Exception(f"Registration failed: {response.text}")

        result = response.json()
        logger.info(f"✓ Registered firmware ID: {result['id']}")
        
        return result

    def list_firmware(
        self,
        device_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list:
        """List all firmware versions.
        
        Args:
            device_type: Filter by device type
            skip: Skip N records
            limit: Limit to N records
            
        Returns:
            List of firmware records
        """
        params = {
            'skip': skip,
            'limit': limit,
        }
        if device_type:
            params['device_type'] = device_type

        response = requests.get(
            f"{self.server_url}/api/ota/admin/firmware",
            params=params,
            headers=self.headers
        )

        if response.status_code != 200:
            raise Exception(f"List failed: {response.text}")

        return response.json()

    def get_firmware(self, firmware_id: int) -> dict:
        """Get firmware details.
        
        Args:
            firmware_id: Firmware ID
            
        Returns:
            Firmware info
        """
        response = requests.get(
            f"{self.server_url}/api/ota/admin/firmware/{firmware_id}",
            headers=self.headers
        )

        if response.status_code != 200:
            raise Exception(f"Get failed: {response.text}")

        return response.json()

    def update_firmware(
        self,
        firmware_id: int,
        is_stable: Optional[bool] = None,
        is_active: Optional[bool] = None,
        description: Optional[str] = None,
        release_notes: Optional[str] = None,
    ) -> dict:
        """Update firmware metadata.
        
        Args:
            firmware_id: Firmware ID
            is_stable: Mark as stable
            is_active: Activate/deactivate
            description: New description
            release_notes: New release notes
            
        Returns:
            Updated firmware info
        """
        payload = {}
        if is_stable is not None:
            payload['is_stable'] = is_stable
        if is_active is not None:
            payload['is_active'] = is_active
        if description is not None:
            payload['description'] = description
        if release_notes is not None:
            payload['release_notes'] = release_notes

        response = requests.patch(
            f"{self.server_url}/api/ota/admin/firmware/{firmware_id}",
            json=payload,
            headers=self.headers
        )

        if response.status_code != 200:
            raise Exception(f"Update failed: {response.text}")

        logger.info(f"✓ Updated firmware {firmware_id}")
        return response.json()

    def deactivate_firmware(self, firmware_id: int) -> dict:
        """Deactivate firmware.
        
        Args:
            firmware_id: Firmware ID
            
        Returns:
            Result info
        """
        response = requests.delete(
            f"{self.server_url}/api/ota/admin/firmware/{firmware_id}",
            headers=self.headers
        )

        if response.status_code != 200:
            raise Exception(f"Deactivation failed: {response.text}")

        logger.info(f"✓ Deactivated firmware {firmware_id}")
        return response.json()

    def get_ota_logs(
        self,
        device_id: Optional[int] = None,
        firmware_id: Optional[int] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list:
        """Get OTA operation logs.
        
        Args:
            device_id: Filter by device ID
            firmware_id: Filter by firmware ID
            status: Filter by status
            skip: Skip N records
            limit: Limit to N records
            
        Returns:
            List of OTA log entries
        """
        params = {
            'skip': skip,
            'limit': limit,
        }
        if device_id:
            params['device_id'] = device_id
        if firmware_id:
            params['firmware_id'] = firmware_id
        if status:
            params['status'] = status

        response = requests.get(
            f"{self.server_url}/api/ota/admin/logs",
            params=params,
            headers=self.headers
        )

        if response.status_code != 200:
            raise Exception(f"Get logs failed: {response.text}")

        return response.json()

    @staticmethod
    def calculate_sha256(file_path: str) -> str:
        """Calculate SHA256 hash of file.
        
        Args:
            file_path: Path to file
            
        Returns:
            Hex digest of SHA256
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for byte_block in iter(lambda: f.read(4096), b''):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()


def main():
    parser = argparse.ArgumentParser(
        description='OTA Firmware Management Tool'
    )
    parser.add_argument(
        '--server',
        default='http://localhost:8000',
        help='OTA server URL (default: http://localhost:8000)'
    )
    parser.add_argument(
        '--admin-token',
        '--token',
        dest='admin_token',
        required=True,
        help='Admin token (X-Admin-Token)'
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # Upload command
    upload_parser = subparsers.add_parser('upload', help='Upload firmware binary')
    upload_parser.add_argument('--file', required=True, help='Path to .bin file')
    upload_parser.add_argument('--device-type', required=True, help='Device type')
    upload_parser.add_argument('--version', required=True, help='Version (e.g., 1.0.0)')

    # Register command
    register_parser = subparsers.add_parser('register', help='Register firmware in database')
    register_parser.add_argument('--device-type', required=True)
    register_parser.add_argument('--version', required=True)
    register_parser.add_argument('--build', type=int, required=True)
    register_parser.add_argument('--filename', required=True)
    register_parser.add_argument('--file-size', type=int, required=True)
    register_parser.add_argument('--file-hash', required=True)
    register_parser.add_argument('--binary-path', required=True)
    register_parser.add_argument('--description')
    register_parser.add_argument('--release-notes')
    register_parser.add_argument('--stable', action='store_true')
    register_parser.add_argument('--min-version')

    # List command
    list_parser = subparsers.add_parser('list', help='List firmware versions')
    list_parser.add_argument('--device-type')
    list_parser.add_argument('--limit', type=int, default=100)

    # Get command
    get_parser = subparsers.add_parser('get', help='Get firmware details')
    get_parser.add_argument('--id', type=int, required=True)

    # Update command
    update_parser = subparsers.add_parser('update', help='Update firmware metadata')
    update_parser.add_argument('--id', type=int, required=True)
    update_parser.add_argument('--stable', action='store_true')
    update_parser.add_argument('--description')

    # Deactivate command
    deactivate_parser = subparsers.add_parser('deactivate', help='Deactivate firmware')
    deactivate_parser.add_argument('--id', type=int, required=True)

    # Logs command
    logs_parser = subparsers.add_parser('logs', help='Get OTA operation logs')
    logs_parser.add_argument('--device-id', type=int)
    logs_parser.add_argument('--firmware-id', type=int)
    logs_parser.add_argument('--status')
    logs_parser.add_argument('--limit', type=int, default=100)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    try:
        manager = OTAManager(args.server, args.admin_token)

        if args.command == 'upload':
            result = manager.upload_firmware(
                args.file,
                args.device_type,
                args.version
            )
            print(json.dumps(result, indent=2))

        elif args.command == 'register':
            result = manager.register_firmware(
                args.device_type,
                args.version,
                args.build,
                args.filename,
                args.file_size,
                args.file_hash,
                args.binary_path,
                args.description,
                args.release_notes,
                args.stable,
                args.min_version,
            )
            print(json.dumps(result, indent=2))

        elif args.command == 'list':
            firmware_list = manager.list_firmware(
                args.device_type,
                limit=args.limit
            )
            print(json.dumps(firmware_list, indent=2, default=str))

        elif args.command == 'get':
            firmware = manager.get_firmware(args.id)
            print(json.dumps(firmware, indent=2, default=str))

        elif args.command == 'update':
            result = manager.update_firmware(
                args.id,
                is_stable=args.stable if hasattr(args, 'stable') else None,
                description=args.description if hasattr(args, 'description') else None,
            )
            print(json.dumps(result, indent=2, default=str))

        elif args.command == 'deactivate':
            result = manager.deactivate_firmware(args.id)
            print(json.dumps(result, indent=2))

        elif args.command == 'logs':
            logs = manager.get_ota_logs(
                args.device_id,
                args.firmware_id,
                args.status,
                limit=args.limit
            )
            print(json.dumps(logs, indent=2, default=str))

        return 0

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
