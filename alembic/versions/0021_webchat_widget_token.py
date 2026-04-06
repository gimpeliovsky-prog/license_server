"""add webchat widget token

Revision ID: 0021_webchat_widget_token
Revises: 0020_buyer_channel_identities
Create Date: 2026-04-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_webchat_widget_token"
down_revision = "0020_buyer_channel_identities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_channels", sa.Column("webchat_widget_token", sa.String(length=128), nullable=True))
    op.create_index(op.f("ix_tenant_channels_webchat_widget_token"), "tenant_channels", ["webchat_widget_token"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_tenant_channels_webchat_widget_token"), table_name="tenant_channels")
    op.drop_column("tenant_channels", "webchat_widget_token")
