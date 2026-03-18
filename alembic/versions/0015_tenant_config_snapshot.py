"""add tenant config snapshot columns

Revision ID: 0015_tenant_config_snapshot
Revises: 0014_process_job_request_key
Create Date: 2026-03-18 16:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_tenant_config_snapshot"
down_revision = "0014_process_job_request_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("tenant_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "tenants",
        sa.Column("tenant_config_revision", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column("tenants", sa.Column("tenant_config_updated_by", sa.String(length=128), nullable=True))
    op.add_column("tenants", sa.Column("tenant_config_updated_at", sa.DateTime(timezone=True), nullable=True))

    op.alter_column("tenants", "tenant_config", server_default=None)
    op.alter_column("tenants", "tenant_config_revision", server_default=None)


def downgrade() -> None:
    op.drop_column("tenants", "tenant_config_updated_at")
    op.drop_column("tenants", "tenant_config_updated_by")
    op.drop_column("tenants", "tenant_config_revision")
    op.drop_column("tenants", "tenant_config")
