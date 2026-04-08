"""ai conversation runtime state

Revision ID: 0023_ai_conversation_runtime_state
Revises: 0022_ai_conversation_transcripts
Create Date: 2026-04-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0023_ai_conversation_runtime_state"
down_revision: Union[str, None] = "0022_ai_conversation_transcripts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_conversations", sa.Column("lead_id", sa.String(length=64), nullable=True))
    op.add_column("ai_conversations", sa.Column("lead_status", sa.String(length=32), nullable=True))
    op.add_column("ai_conversations", sa.Column("lead_temperature", sa.String(length=16), nullable=True))
    op.add_column("ai_conversations", sa.Column("next_action", sa.String(length=64), nullable=True))
    op.add_column("ai_conversations", sa.Column("handoff_reason", sa.String(length=64), nullable=True))
    op.add_column("ai_conversations", sa.Column("sales_owner_status", sa.String(length=64), nullable=True))
    op.add_column("ai_conversations", sa.Column("quality_score", sa.Integer(), nullable=True))
    op.add_column("ai_conversations", sa.Column("quality_flags_json", sa.JSON(), nullable=True))
    op.add_column("ai_conversations", sa.Column("last_event_type", sa.String(length=64), nullable=True))
    op.add_column("ai_conversations", sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_conversations", sa.Column("last_delivery_status", sa.String(length=32), nullable=True))
    op.create_index(op.f("ix_ai_conversations_lead_id"), "ai_conversations", ["lead_id"], unique=False)
    op.create_index(op.f("ix_ai_conversations_lead_status"), "ai_conversations", ["lead_status"], unique=False)
    op.create_index(op.f("ix_ai_conversations_lead_temperature"), "ai_conversations", ["lead_temperature"], unique=False)
    op.create_index(op.f("ix_ai_conversations_sales_owner_status"), "ai_conversations", ["sales_owner_status"], unique=False)
    op.create_index(op.f("ix_ai_conversations_last_event_at"), "ai_conversations", ["last_event_at"], unique=False)
    op.create_index(op.f("ix_ai_conversations_last_delivery_status"), "ai_conversations", ["last_delivery_status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_conversations_last_delivery_status"), table_name="ai_conversations")
    op.drop_index(op.f("ix_ai_conversations_last_event_at"), table_name="ai_conversations")
    op.drop_index(op.f("ix_ai_conversations_sales_owner_status"), table_name="ai_conversations")
    op.drop_index(op.f("ix_ai_conversations_lead_temperature"), table_name="ai_conversations")
    op.drop_index(op.f("ix_ai_conversations_lead_status"), table_name="ai_conversations")
    op.drop_index(op.f("ix_ai_conversations_lead_id"), table_name="ai_conversations")
    op.drop_column("ai_conversations", "last_delivery_status")
    op.drop_column("ai_conversations", "last_event_at")
    op.drop_column("ai_conversations", "last_event_type")
    op.drop_column("ai_conversations", "quality_flags_json")
    op.drop_column("ai_conversations", "quality_score")
    op.drop_column("ai_conversations", "sales_owner_status")
    op.drop_column("ai_conversations", "handoff_reason")
    op.drop_column("ai_conversations", "next_action")
    op.drop_column("ai_conversations", "lead_temperature")
    op.drop_column("ai_conversations", "lead_status")
    op.drop_column("ai_conversations", "lead_id")
