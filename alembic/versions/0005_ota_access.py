"""Add OTA access table

Revision ID: 0005_ota_access
Revises: 0004_firmware_ota
Create Date: 2026-01-26 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0005_ota_access"
down_revision = "0004_firmware_ota"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ota_access",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("license_key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["license_key_id"], ["license_keys.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("ota_access")
