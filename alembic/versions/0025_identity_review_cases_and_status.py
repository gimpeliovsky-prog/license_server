"""identity review cases and recognition status

Revision ID: 0025_identity_review_cases_and_status
Revises: 0024_erp_crm_identity_sync
Create Date: 2026-04-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0025_identity_review_cases_and_status"
down_revision: Union[str, None] = "0024_erp_crm_identity_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("buyer_channel_identities", sa.Column("recognition_status", sa.String(length=64), nullable=True))
    op.add_column("buyer_channel_identities", sa.Column("match_confidence", sa.String(length=16), nullable=True))
    op.add_column("buyer_channel_identities", sa.Column("source", sa.String(length=64), nullable=True))
    op.add_column("buyer_channel_identities", sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("buyer_channel_identities", sa.Column("review_reason", sa.String(length=64), nullable=True))
    op.add_column("buyer_channel_identities", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_buyer_channel_identities_recognition_status"), "buyer_channel_identities", ["recognition_status"], unique=False)
    op.create_index(op.f("ix_buyer_channel_identities_needs_review"), "buyer_channel_identities", ["needs_review"], unique=False)

    op.create_table(
        "identity_review_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("buyer_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=True),
        sa.Column("channel_user_id", sa.String(length=128), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("company_query", sa.String(length=255), nullable=True),
        sa.Column("company_registry_number", sa.String(length=32), nullable=True),
        sa.Column("official_company_name", sa.String(length=255), nullable=True),
        sa.Column("matched_erp_customer_id", sa.String(length=140), nullable=True),
        sa.Column("matched_erp_customer_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("requires_operator", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["buyer_identity_id"], ["buyer_channel_identities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_identity_review_cases_tenant_id"), "identity_review_cases", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_identity_review_cases_buyer_identity_id"), "identity_review_cases", ["buyer_identity_id"], unique=False)
    op.create_index(op.f("ix_identity_review_cases_channel"), "identity_review_cases", ["channel"], unique=False)
    op.create_index(op.f("ix_identity_review_cases_channel_user_id"), "identity_review_cases", ["channel_user_id"], unique=False)
    op.create_index(op.f("ix_identity_review_cases_phone"), "identity_review_cases", ["phone"], unique=False)
    op.create_index(op.f("ix_identity_review_cases_company_registry_number"), "identity_review_cases", ["company_registry_number"], unique=False)
    op.create_index(op.f("ix_identity_review_cases_matched_erp_customer_id"), "identity_review_cases", ["matched_erp_customer_id"], unique=False)
    op.create_index(op.f("ix_identity_review_cases_status"), "identity_review_cases", ["status"], unique=False)
    op.create_index(op.f("ix_identity_review_cases_reason"), "identity_review_cases", ["reason"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_identity_review_cases_reason"), table_name="identity_review_cases")
    op.drop_index(op.f("ix_identity_review_cases_status"), table_name="identity_review_cases")
    op.drop_index(op.f("ix_identity_review_cases_matched_erp_customer_id"), table_name="identity_review_cases")
    op.drop_index(op.f("ix_identity_review_cases_company_registry_number"), table_name="identity_review_cases")
    op.drop_index(op.f("ix_identity_review_cases_phone"), table_name="identity_review_cases")
    op.drop_index(op.f("ix_identity_review_cases_channel_user_id"), table_name="identity_review_cases")
    op.drop_index(op.f("ix_identity_review_cases_channel"), table_name="identity_review_cases")
    op.drop_index(op.f("ix_identity_review_cases_buyer_identity_id"), table_name="identity_review_cases")
    op.drop_index(op.f("ix_identity_review_cases_tenant_id"), table_name="identity_review_cases")
    op.drop_table("identity_review_cases")

    op.drop_index(op.f("ix_buyer_channel_identities_needs_review"), table_name="buyer_channel_identities")
    op.drop_index(op.f("ix_buyer_channel_identities_recognition_status"), table_name="buyer_channel_identities")
    op.drop_column("buyer_channel_identities", "last_verified_at")
    op.drop_column("buyer_channel_identities", "review_reason")
    op.drop_column("buyer_channel_identities", "needs_review")
    op.drop_column("buyer_channel_identities", "source")
    op.drop_column("buyer_channel_identities", "match_confidence")
    op.drop_column("buyer_channel_identities", "recognition_status")
