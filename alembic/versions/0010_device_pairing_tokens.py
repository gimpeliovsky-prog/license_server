"""add device pairing tokens table

Revision ID: 0010_device_pairing_tokens
Revises: 0009_erp_users
Create Date: 2026-03-04 22:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0010_device_pairing_tokens"
down_revision = "0009_erp_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_pairing_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("erp_username", sa.String(length=140), nullable=False),
        sa.Column("erp_roles", sa.JSON(), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_device_id", sa.String(length=128), nullable=True),
        sa.Column("created_ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_device_pairing_tokens_tenant_id_tenants"),
        sa.UniqueConstraint("token_hash", name="uq_device_pairing_tokens_token_hash"),
    )
    op.create_index(op.f("ix_device_pairing_tokens_tenant_id"), "device_pairing_tokens", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_device_pairing_tokens_token_hash"), "device_pairing_tokens", ["token_hash"], unique=True)
    op.create_index(op.f("ix_device_pairing_tokens_expires_at"), "device_pairing_tokens", ["expires_at"], unique=False)
    op.create_index(op.f("ix_device_pairing_tokens_used_at"), "device_pairing_tokens", ["used_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_device_pairing_tokens_used_at"), table_name="device_pairing_tokens")
    op.drop_index(op.f("ix_device_pairing_tokens_expires_at"), table_name="device_pairing_tokens")
    op.drop_index(op.f("ix_device_pairing_tokens_token_hash"), table_name="device_pairing_tokens")
    op.drop_index(op.f("ix_device_pairing_tokens_tenant_id"), table_name="device_pairing_tokens")
    op.drop_table("device_pairing_tokens")
