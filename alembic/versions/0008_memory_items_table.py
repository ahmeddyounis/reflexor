"""Add memory_items table.

Revision ID: 0008_memory_items_table
Revises: 0007_event_suppressions_clear_audit
Create Date: 2026-03-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB, "postgresql")

revision = "0008_memory_items_table"
down_revision = "0007_event_suppressions_clear_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_items",
        sa.Column("memory_id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.run_id"), nullable=False),
        sa.Column("event_id", sa.String(), sa.ForeignKey("events.event_id"), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=True),
        sa.Column("event_source", sa.String(), nullable=True),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("content", JSON_TYPE, nullable=False),
        sa.Column("tags", JSON_TYPE, nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
    )
    op.create_index("ix_memory_items_run_id", "memory_items", ["run_id"], unique=True)
    op.create_index("ix_memory_items_kind", "memory_items", ["kind"])
    op.create_index("ix_memory_items_event_type", "memory_items", ["event_type"])
    op.create_index("ix_memory_items_event_source", "memory_items", ["event_source"])
    op.create_index("ix_memory_items_created_at_ms", "memory_items", ["created_at_ms"])
    op.create_index("ix_memory_items_updated_at_ms", "memory_items", ["updated_at_ms"])


def downgrade() -> None:
    op.drop_index("ix_memory_items_updated_at_ms", table_name="memory_items")
    op.drop_index("ix_memory_items_created_at_ms", table_name="memory_items")
    op.drop_index("ix_memory_items_event_source", table_name="memory_items")
    op.drop_index("ix_memory_items_event_type", table_name="memory_items")
    op.drop_index("ix_memory_items_kind", table_name="memory_items")
    op.drop_index("ix_memory_items_run_id", table_name="memory_items")
    op.drop_table("memory_items")
