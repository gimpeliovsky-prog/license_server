"""add erp idempotency table

Revision ID: 0008_erp_idempotency
Revises: 0007_tenant_company_name
Create Date: 2026-03-04 15:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0008_erp_idempotency"
down_revision = "0007_tenant_company_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "erp_idempotency",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_content_type", sa.String(length=255), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_erp_idempotency_tenant_id_tenants"),
        sa.UniqueConstraint("tenant_id", "method", "endpoint", "idempotency_key", name="uq_erp_idempotency_entry"),
    )
    op.create_index(op.f("ix_erp_idempotency_tenant_id"), "erp_idempotency", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_erp_idempotency_tenant_id"), table_name="erp_idempotency")
    op.drop_table("erp_idempotency")
