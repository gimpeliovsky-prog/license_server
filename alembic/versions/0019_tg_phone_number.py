"""add telegram phone number to tenant channels

Revision ID: 0019_tg_phone_number
Revises: 0018_tenant_channels
Create Date: 2026-03-30 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_tg_phone_number"
down_revision = "0018_tenant_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_channels", sa.Column("tg_phone_number", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant_channels", "tg_phone_number")
