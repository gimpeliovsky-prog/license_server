"""add is_system flag to tenants

Revision ID: 0012_tenant_is_system
Revises: 0011_license_description
Create Date: 2026-03-06 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0012_tenant_is_system"
down_revision = "0011_license_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("tenants", "is_system")
