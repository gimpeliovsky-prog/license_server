"""Add firmware OTA tables

Revision ID: 0004_firmware_ota
Revises: 0003_license_key_fingerprint
Create Date: 2026-01-18 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_firmware_ota"
down_revision = "0003_license_key_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create firmware table
    op.create_table(
        "firmware",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_type", sa.String(length=50), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("build_number", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("binary_path", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("release_notes", sa.Text(), nullable=True),
        sa.Column("is_stable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("min_current_version", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_firmware_device_type"), "firmware", ["device_type"], unique=False
    )
    op.create_index(
        op.f("ix_firmware_filename"), "firmware", ["filename"], unique=True
    )

    # Create device OTA log table
    op.create_table(
        "device_ota_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("firmware_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("bytes_downloaded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("download_started_at", sa.DateTime(), nullable=True),
        sa.Column("download_completed_at", sa.DateTime(), nullable=True),
        sa.Column("installed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["firmware_id"], ["firmware.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_device_ota_log_device_id"), "device_ota_log", ["device_id"], unique=False
    )
    op.create_index(
        op.f("ix_device_ota_log_firmware_id"), "device_ota_log", ["firmware_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_device_ota_log_firmware_id"), table_name="device_ota_log")
    op.drop_index(op.f("ix_device_ota_log_device_id"), table_name="device_ota_log")
    op.drop_table("device_ota_log")
    op.drop_index(op.f("ix_firmware_filename"), table_name="firmware")
    op.drop_index(op.f("ix_firmware_device_type"), table_name="firmware")
    op.drop_table("firmware")
