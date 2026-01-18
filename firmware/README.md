# Firmware Storage Directory

This directory stores OTA firmware binaries for ESP32 devices.

## Structure

```
firmware/
├── scales_bridge/          # Device type directory
│   ├── v1.0.0.bin         # Firmware binary files
│   ├── v1.1.0.bin
│   └── v2.0.0.bin
└── other_device_type/      # Other device types
    ├── v0.9.0.bin
    └── v1.0.0.bin
```

## Adding New Firmware

1. **Upload binary via API:**
   ```bash
   curl -F "file=@/path/to/firmware.bin" \
        -F "device_type=scales_bridge" \
        -F "version=1.0.0" \
        http://localhost:8000/api/ota/admin/upload
   ```

2. **Register in database:**
   ```bash
   curl -X POST http://localhost:8000/api/ota/admin/firmware \
        -H "Content-Type: application/json" \
        -d '{
          "device_type": "scales_bridge",
          "version": "1.0.0",
          "build_number": 1,
          "filename": "firmware.bin",
          "file_size": 524288,
          "file_hash": "sha256_hex_string",
          "binary_path": "scales_bridge/v1.0.0.bin",
          "is_stable": true,
          "description": "Initial release"
        }'
   ```

## File Naming Convention

- Format: `v{MAJOR}.{MINOR}.{PATCH}.bin`
- Example: `v1.2.3.bin`
- Must match semantic versioning in database record

## Storage Limits

- Individual file size limit: up to 2GB
- Total directory size: manage based on storage capacity
- Archive old versions if space is needed

## Security Notes

- Files are served with proper MIME types
- Access controlled via authentication
- Hash verification on download
- Never store sensitive data in firmware files
