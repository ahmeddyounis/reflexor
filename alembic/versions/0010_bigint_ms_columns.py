"""Widen millisecond timestamp columns to BIGINT.

Revision ID: 0010_bigint_ms_columns
Revises: 0009_event_dedupe_ledger
Create Date: 2026-03-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_bigint_ms_columns"
down_revision = "0009_event_dedupe_ledger"
branch_labels = None
depends_on = None


_MS_COLUMNS: dict[str, tuple[tuple[str, bool], ...]] = {
    "events": (("received_at_ms", False),),
    "runs": (
        ("created_at_ms", False),
        ("started_at_ms", True),
        ("completed_at_ms", True),
    ),
    "tool_calls": (
        ("created_at_ms", False),
        ("started_at_ms", True),
        ("completed_at_ms", True),
    ),
    "tasks": (
        ("created_at_ms", False),
        ("started_at_ms", True),
        ("completed_at_ms", True),
    ),
    "approvals": (("created_at_ms", False), ("decided_at_ms", True)),
    "run_packets": (("created_at_ms", False),),
    "event_suppressions": (
        ("window_start_ms", False),
        ("suppressed_until_ms", True),
        ("cleared_at_ms", True),
        ("created_at_ms", False),
        ("updated_at_ms", False),
        ("expires_at_ms", False),
    ),
    "memory_items": (("created_at_ms", False), ("updated_at_ms", False)),
    "event_dedupes": (
        ("created_at_ms", False),
        ("updated_at_ms", False),
        ("expires_at_ms", False),
    ),
    "idempotency_ledger": (
        ("created_at_ms", False),
        ("updated_at_ms", False),
        ("expires_at_ms", True),
    ),
}


def _alter_ms_columns(*, to_type: sa.types.TypeEngine[int]) -> None:
    for table_name, columns in _MS_COLUMNS.items():
        with op.batch_alter_table(table_name) as batch_op:
            for column_name, nullable in columns:
                batch_op.alter_column(
                    column_name,
                    existing_type=sa.Integer(),
                    type_=to_type,
                    existing_nullable=nullable,
                )


def upgrade() -> None:
    _alter_ms_columns(to_type=sa.BigInteger())


def downgrade() -> None:
    _alter_ms_columns(to_type=sa.Integer())
