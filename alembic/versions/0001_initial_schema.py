"""Initial schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-02-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("received_at_ms", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=True),
    )
    op.create_index("ix_events_type", "events", ["type"])

    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(), primary_key=True),
        sa.Column("parent_run_id", sa.String(), nullable=True),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("started_at_ms", sa.Integer(), nullable=True),
        sa.Column("completed_at_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_runs_created_at_ms", "runs", ["created_at_ms"])

    op.create_table(
        "tool_calls",
        sa.Column("tool_call_id", sa.String(), primary_key=True),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("permission_scope", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("started_at_ms", sa.Integer(), nullable=True),
        sa.Column("completed_at_ms", sa.Integer(), nullable=True),
        sa.Column("result_ref", sa.String(), nullable=True),
    )
    op.create_index("ix_tool_calls_idempotency_key", "tool_calls", ["idempotency_key"])

    op.create_table(
        "tasks",
        sa.Column("task_id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.run_id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("tool_call_id", sa.String(), sa.ForeignKey("tool_calls.tool_call_id")),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("timeout_s", sa.Integer(), nullable=False),
        sa.Column("depends_on", sa.JSON(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("started_at_ms", sa.Integer(), nullable=True),
        sa.Column("completed_at_ms", sa.Integer(), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
    )
    op.create_index("ix_tasks_run_id", "tasks", ["run_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])

    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.run_id"), nullable=False),
        sa.Column("task_id", sa.String(), sa.ForeignKey("tasks.task_id"), nullable=False),
        sa.Column(
            "tool_call_id",
            sa.String(),
            sa.ForeignKey("tool_calls.tool_call_id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("decided_at_ms", sa.Integer(), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("payload_hash", sa.String(), nullable=True),
        sa.Column("preview", sa.String(), nullable=True),
    )
    op.create_index("ix_approvals_status", "approvals", ["status"])

    op.create_table(
        "run_packets",
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.run_id"), primary_key=True),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("packet", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("run_packets")
    op.drop_index("ix_approvals_status", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_run_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_tool_calls_idempotency_key", table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_index("ix_runs_created_at_ms", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_events_type", table_name="events")
    op.drop_table("events")
