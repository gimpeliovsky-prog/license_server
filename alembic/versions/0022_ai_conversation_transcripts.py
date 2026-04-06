"""ai conversation transcripts

Revision ID: 0022_ai_conversation_transcripts
Revises: 0021_webchat_widget_token
Create Date: 2026-04-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0022_ai_conversation_transcripts"
down_revision: Union[str, None] = "0021_webchat_widget_token"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("buyer_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", sa.String(length=190), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("channel_user_id", sa.String(length=128), nullable=False),
        sa.Column("erp_customer_id", sa.String(length=140), nullable=True),
        sa.Column("buyer_name", sa.String(length=255), nullable=True),
        sa.Column("buyer_phone", sa.String(length=32), nullable=True),
        sa.Column("first_customer_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_stage", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["buyer_identity_id"], ["buyer_channel_identities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "session_id", name="uq_ai_conversation_session"),
    )
    op.create_index(op.f("ix_ai_conversations_tenant_id"), "ai_conversations", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_ai_conversations_buyer_identity_id"), "ai_conversations", ["buyer_identity_id"], unique=False)
    op.create_index(op.f("ix_ai_conversations_channel_type"), "ai_conversations", ["channel_type"], unique=False)
    op.create_index(op.f("ix_ai_conversations_channel_user_id"), "ai_conversations", ["channel_user_id"], unique=False)
    op.create_index(op.f("ix_ai_conversations_erp_customer_id"), "ai_conversations", ["erp_customer_id"], unique=False)
    op.create_index(op.f("ix_ai_conversations_last_message_at"), "ai_conversations", ["last_message_at"], unique=False)

    op.create_table(
        "ai_conversation_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", sa.String(length=190), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("message_type", sa.String(length=32), nullable=False, server_default="chat"),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("behavior_class", sa.String(length=64), nullable=True),
        sa.Column("tool_name", sa.String(length=128), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "message_id", name="uq_ai_conversation_message"),
    )
    op.create_index(op.f("ix_ai_conversation_messages_conversation_id"), "ai_conversation_messages", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_ai_conversation_messages_role"), "ai_conversation_messages", ["role"], unique=False)
    op.create_index(op.f("ix_ai_conversation_messages_created_at"), "ai_conversation_messages", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_conversation_messages_created_at"), table_name="ai_conversation_messages")
    op.drop_index(op.f("ix_ai_conversation_messages_role"), table_name="ai_conversation_messages")
    op.drop_index(op.f("ix_ai_conversation_messages_conversation_id"), table_name="ai_conversation_messages")
    op.drop_table("ai_conversation_messages")
    op.drop_index(op.f("ix_ai_conversations_last_message_at"), table_name="ai_conversations")
    op.drop_index(op.f("ix_ai_conversations_erp_customer_id"), table_name="ai_conversations")
    op.drop_index(op.f("ix_ai_conversations_channel_user_id"), table_name="ai_conversations")
    op.drop_index(op.f("ix_ai_conversations_channel_type"), table_name="ai_conversations")
    op.drop_index(op.f("ix_ai_conversations_buyer_identity_id"), table_name="ai_conversations")
    op.drop_index(op.f("ix_ai_conversations_tenant_id"), table_name="ai_conversations")
    op.drop_table("ai_conversations")
