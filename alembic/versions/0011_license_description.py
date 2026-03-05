"""add description to license keys

Revision ID: 0011_license_description
Revises: 0010_device_pairing_tokens
Create Date: 2026-03-05 12:20:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0011_license_description"
down_revision = "0010_device_pairing_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("license_keys", sa.Column("description", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("license_keys", "description")
