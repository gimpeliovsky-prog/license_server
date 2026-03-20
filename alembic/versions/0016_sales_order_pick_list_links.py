"""add sales order pick list links

Revision ID: 0016_sales_order_pick_list_links
Revises: 0015_tenant_config_snapshot
Create Date: 2026-03-20 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0016_sales_order_pick_list_links"
down_revision = "0015_tenant_config_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_order_pick_lists",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sales_order_name", sa.String(length=140), nullable=False),
        sa.Column("pick_list_name", sa.String(length=140), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "pick_list_name", name="uq_sales_order_pick_list_pick_list"),
        sa.UniqueConstraint("tenant_id", "sales_order_name", name="uq_sales_order_pick_list_sales_order"),
    )
    op.create_index(
        op.f("ix_sales_order_pick_lists_tenant_id"),
        "sales_order_pick_lists",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sales_order_pick_lists_tenant_id"), table_name="sales_order_pick_lists")
    op.drop_table("sales_order_pick_lists")
