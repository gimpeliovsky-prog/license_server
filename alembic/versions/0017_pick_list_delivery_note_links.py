"""allow many pick lists per sales order and link delivery notes

Revision ID: 0017_pick_list_dn_links
Revises: 0016_sales_order_pick_list_links
Create Date: 2026-03-20 13:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0017_pick_list_dn_links"
down_revision = "0016_sales_order_pick_list_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_sales_order_pick_list_sales_order",
        "sales_order_pick_lists",
        type_="unique",
    )

    op.create_table(
        "pick_list_delivery_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pick_list_name", sa.String(length=140), nullable=False),
        sa.Column("delivery_note_name", sa.String(length=140), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "delivery_note_name", name="uq_pick_list_delivery_note_delivery_note"),
        sa.UniqueConstraint("tenant_id", "pick_list_name", name="uq_pick_list_delivery_note_pick_list"),
    )
    op.create_index(
        op.f("ix_pick_list_delivery_notes_tenant_id"),
        "pick_list_delivery_notes",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_pick_list_delivery_notes_tenant_id"), table_name="pick_list_delivery_notes")
    op.drop_table("pick_list_delivery_notes")
    op.create_unique_constraint(
        "uq_sales_order_pick_list_sales_order",
        "sales_order_pick_lists",
        ["tenant_id", "sales_order_name"],
    )
