"""Replace permanent event dedupe index with retention-window ledger.

Revision ID: 0009_event_dedupe_ledger
Revises: 0008_memory_items_table
Create Date: 2026-03-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_event_dedupe_ledger"
down_revision = "0008_memory_items_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ux_events_source_dedupe_key", table_name="events")

    op.create_table(
        "event_dedupes",
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.Column("expires_at_ms", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.event_id"]),
        sa.PrimaryKeyConstraint("source", "dedupe_key"),
    )
    op.create_index(
        "ix_event_dedupes_expires_at_ms",
        "event_dedupes",
        ["expires_at_ms"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_event_dedupes_expires_at_ms", table_name="event_dedupes")
    op.drop_table("event_dedupes")

    op.create_index(
        "ux_events_source_dedupe_key",
        "events",
        ["source", "dedupe_key"],
        unique=True,
    )
