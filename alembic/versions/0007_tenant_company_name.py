"""Add company name to tenants

Revision ID: 0007_tenant_company_name
Revises: 0006_license_fp_uniq
Create Date: 2026-01-26 12:30:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0007_tenant_company_name"
down_revision = "0006_license_fp_uniq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("company_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "company_name")
