"""add buyer channel identities

Revision ID: 0020_buyer_channel_identities
Revises: 0019_tg_phone_number
Create Date: 2026-03-30 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0020_buyer_channel_identities"
down_revision = "0019_tg_phone_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "buyer_channel_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("channel_user_id", sa.String(length=128), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("erp_customer_id", sa.String(length=140), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "channel", "channel_user_id", name="uq_buyer_channel_identity"),
    )
    op.create_index(op.f("ix_buyer_channel_identities_tenant_id"), "buyer_channel_identities", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_buyer_channel_identities_tenant_id"), table_name="buyer_channel_identities")
    op.drop_table("buyer_channel_identities")
