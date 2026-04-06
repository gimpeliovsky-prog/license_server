"""add tenant channels table

Revision ID: 0018_tenant_channels
Revises: 0017_pick_list_dn_links
Create Date: 2026-03-29 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0018_tenant_channels"
down_revision = "0017_pick_list_dn_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_channels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("tg_bot_token", sa.String(128), nullable=True),
        sa.Column("tg_bot_username", sa.String(64), nullable=True),
        sa.Column("tg_webhook_secret", sa.String(128), nullable=True),
        sa.Column("wa_account_sid", sa.String(64), nullable=True),
        sa.Column("wa_auth_token", sa.String(64), nullable=True),
        sa.Column("wa_number", sa.String(32), nullable=True),
        sa.Column("webchat_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("webchat_allowed_origins", sa.Text(), nullable=True),
        sa.Column("ai_system_prompt", sa.Text(), nullable=True),
        sa.Column("ai_language", sa.String(8), nullable=False, server_default="ru"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_tenant_channels_tg_bot_token", "tenant_channels", ["tg_bot_token"], unique=True)
    op.create_index("ix_tenant_channels_wa_number", "tenant_channels", ["wa_number"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_tenant_channels_wa_number", table_name="tenant_channels")
    op.drop_index("ix_tenant_channels_tg_bot_token", table_name="tenant_channels")
    op.drop_table("tenant_channels")
