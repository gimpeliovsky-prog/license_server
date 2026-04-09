"""erp crm identity sync snapshots

Revision ID: 0024_erp_crm_identity_sync
Revises: 0023_ai_conv_runtime_state
Create Date: 2026-04-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0024_erp_crm_identity_sync"
down_revision: Union[str, None] = "0023_ai_conv_runtime_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("buyer_channel_identities", sa.Column("erp_contact_id", sa.String(length=140), nullable=True))
    op.add_column("buyer_channel_identities", sa.Column("erp_customer_name", sa.String(length=255), nullable=True))
    op.alter_column("buyer_channel_identities", "erp_customer_id", existing_type=sa.String(length=140), nullable=True)
    op.create_index(op.f("ix_buyer_channel_identities_erp_contact_id"), "buyer_channel_identities", ["erp_contact_id"], unique=False)
    op.create_index(op.f("ix_buyer_channel_identities_erp_customer_id"), "buyer_channel_identities", ["erp_customer_id"], unique=False)

    op.create_table(
        "erp_customer_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("erp_customer_id", sa.String(length=140), nullable=False),
        sa.Column("customer_name", sa.String(length=255), nullable=True),
        sa.Column("customer_type", sa.String(length=64), nullable=True),
        sa.Column("customer_group", sa.String(length=140), nullable=True),
        sa.Column("territory", sa.String(length=140), nullable=True),
        sa.Column("modified_at_erp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "erp_customer_id", name="uq_erp_customer_snapshot"),
    )
    op.create_index(op.f("ix_erp_customer_snapshots_tenant_id"), "erp_customer_snapshots", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_erp_customer_snapshots_erp_customer_id"), "erp_customer_snapshots", ["erp_customer_id"], unique=False)
    op.create_index(op.f("ix_erp_customer_snapshots_modified_at_erp"), "erp_customer_snapshots", ["modified_at_erp"], unique=False)

    op.create_table(
        "erp_contact_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("erp_contact_id", sa.String(length=140), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=140), nullable=True),
        sa.Column("last_name", sa.String(length=140), nullable=True),
        sa.Column("email_id", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("mobile_no", sa.String(length=64), nullable=True),
        sa.Column("normalized_phone", sa.String(length=32), nullable=True),
        sa.Column("normalized_mobile_no", sa.String(length=32), nullable=True),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("modified_at_erp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "erp_contact_id", name="uq_erp_contact_snapshot"),
    )
    op.create_index(op.f("ix_erp_contact_snapshots_tenant_id"), "erp_contact_snapshots", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_erp_contact_snapshots_erp_contact_id"), "erp_contact_snapshots", ["erp_contact_id"], unique=False)
    op.create_index(op.f("ix_erp_contact_snapshots_normalized_phone"), "erp_contact_snapshots", ["normalized_phone"], unique=False)
    op.create_index(op.f("ix_erp_contact_snapshots_normalized_mobile_no"), "erp_contact_snapshots", ["normalized_mobile_no"], unique=False)
    op.create_index(op.f("ix_erp_contact_snapshots_modified_at_erp"), "erp_contact_snapshots", ["modified_at_erp"], unique=False)

    op.create_table(
        "erp_contact_customer_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("erp_contact_id", sa.String(length=140), nullable=False),
        sa.Column("erp_customer_id", sa.String(length=140), nullable=False),
        sa.Column("modified_at_erp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "erp_contact_id", "erp_customer_id", name="uq_erp_contact_customer_link"),
    )
    op.create_index(op.f("ix_erp_contact_customer_links_tenant_id"), "erp_contact_customer_links", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_erp_contact_customer_links_erp_contact_id"), "erp_contact_customer_links", ["erp_contact_id"], unique=False)
    op.create_index(op.f("ix_erp_contact_customer_links_erp_customer_id"), "erp_contact_customer_links", ["erp_customer_id"], unique=False)
    op.create_index(op.f("ix_erp_contact_customer_links_modified_at_erp"), "erp_contact_customer_links", ["modified_at_erp"], unique=False)

    op.create_table(
        "erp_crm_sync_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stream_name", sa.String(length=64), nullable=False),
        sa.Column("cursor_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_name", sa.String(length=140), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_text", sa.Text(), nullable=True),
        sa.Column("last_delta_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "stream_name", name="uq_erp_crm_sync_state"),
    )
    op.create_index(op.f("ix_erp_crm_sync_states_tenant_id"), "erp_crm_sync_states", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_erp_crm_sync_states_tenant_id"), table_name="erp_crm_sync_states")
    op.drop_table("erp_crm_sync_states")

    op.drop_index(op.f("ix_erp_contact_customer_links_modified_at_erp"), table_name="erp_contact_customer_links")
    op.drop_index(op.f("ix_erp_contact_customer_links_erp_customer_id"), table_name="erp_contact_customer_links")
    op.drop_index(op.f("ix_erp_contact_customer_links_erp_contact_id"), table_name="erp_contact_customer_links")
    op.drop_index(op.f("ix_erp_contact_customer_links_tenant_id"), table_name="erp_contact_customer_links")
    op.drop_table("erp_contact_customer_links")

    op.drop_index(op.f("ix_erp_contact_snapshots_modified_at_erp"), table_name="erp_contact_snapshots")
    op.drop_index(op.f("ix_erp_contact_snapshots_normalized_mobile_no"), table_name="erp_contact_snapshots")
    op.drop_index(op.f("ix_erp_contact_snapshots_normalized_phone"), table_name="erp_contact_snapshots")
    op.drop_index(op.f("ix_erp_contact_snapshots_erp_contact_id"), table_name="erp_contact_snapshots")
    op.drop_index(op.f("ix_erp_contact_snapshots_tenant_id"), table_name="erp_contact_snapshots")
    op.drop_table("erp_contact_snapshots")

    op.drop_index(op.f("ix_erp_customer_snapshots_modified_at_erp"), table_name="erp_customer_snapshots")
    op.drop_index(op.f("ix_erp_customer_snapshots_erp_customer_id"), table_name="erp_customer_snapshots")
    op.drop_index(op.f("ix_erp_customer_snapshots_tenant_id"), table_name="erp_customer_snapshots")
    op.drop_table("erp_customer_snapshots")

    op.drop_index(op.f("ix_buyer_channel_identities_erp_customer_id"), table_name="buyer_channel_identities")
    op.drop_index(op.f("ix_buyer_channel_identities_erp_contact_id"), table_name="buyer_channel_identities")
    op.alter_column("buyer_channel_identities", "erp_customer_id", existing_type=sa.String(length=140), nullable=False)
    op.drop_column("buyer_channel_identities", "erp_customer_name")
    op.drop_column("buyer_channel_identities", "erp_contact_id")
