"""Add event_suppressions table.

Revision ID: 0006_event_suppressions_table
Revises: 0005_json_columns_to_jsonb_postgres
Create Date: 2026-03-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB, "postgresql")

revision = "0006_event_suppressions_table"
down_revision = "0005_json_columns_to_jsonb_postgres"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_suppressions",
        sa.Column("signature_hash", sa.String(), primary_key=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("event_source", sa.String(), nullable=False),
        sa.Column("signature", JSON_TYPE, nullable=False),
        sa.Column("window_start_ms", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("window_ms", sa.Integer(), nullable=False),
        sa.Column("suppressed_until_ms", sa.Integer(), nullable=True),
        sa.Column("resume_required", sa.Boolean(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.Column("expires_at_ms", sa.Integer(), nullable=False),
    )

    op.create_index("ix_event_suppressions_event_type", "event_suppressions", ["event_type"])
    op.create_index("ix_event_suppressions_event_source", "event_suppressions", ["event_source"])
    op.create_index(
        "ix_event_suppressions_suppressed_until_ms",
        "event_suppressions",
        ["suppressed_until_ms"],
    )
    op.create_index(
        "ix_event_suppressions_expires_at_ms",
        "event_suppressions",
        ["expires_at_ms"],
    )


def downgrade() -> None:
    op.drop_index("ix_event_suppressions_expires_at_ms", table_name="event_suppressions")
    op.drop_index("ix_event_suppressions_suppressed_until_ms", table_name="event_suppressions")
    op.drop_index("ix_event_suppressions_event_source", table_name="event_suppressions")
    op.drop_index("ix_event_suppressions_event_type", table_name="event_suppressions")
    op.drop_table("event_suppressions")
