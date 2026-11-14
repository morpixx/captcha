"""init schema

Revision ID: 0001_init
Revises:
Create Date: 2026-11-14 00:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_init"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("api_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column(
            "balance",
            sa.Numeric(18, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "disabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("api_key", name="uq_accounts_api_key"),
    )
    op.create_index("ix_accounts_api_key", "accounts", ["api_key"], unique=True)

    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "cost",
            sa.Numeric(18, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("retries", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_tasks_account_id", "tasks", ["account_id"])
    op.create_index("ix_tasks_type", "tasks", ["type"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"])
    op.create_index("ix_tasks_account_created", "tasks", ["account_id", "created_at"])
    op.create_index("ix_tasks_status_created", "tasks", ["status", "created_at"])
    op.create_index("ix_tasks_type_status", "tasks", ["type", "status"])

    op.create_table(
        "task_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="INFO"),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_task_logs_task_id", "task_logs", ["task_id"])
    op.create_index("ix_task_logs_task_ts", "task_logs", ["task_id", "ts"])

    op.create_table(
        "solver_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("solver_name", sa.String(length=64), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_solver_runs_solver_name", "solver_runs", ["solver_name"])
    op.create_index("ix_solver_runs_task_type", "solver_runs", ["task_type"])
    op.create_index("ix_solver_runs_error_code", "solver_runs", ["error_code"])
    op.create_index("ix_solver_runs_task_id", "solver_runs", ["task_id"])
    op.create_index("ix_solver_runs_name_started", "solver_runs", ["solver_name", "started_at"])
    op.create_index("ix_solver_runs_type_started", "solver_runs", ["task_type", "started_at"])

    op.create_table(
        "sensor_schema_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=128), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("notes", sa.String(length=1024), nullable=True),
    )
    op.create_index("ix_sensor_schema_versions_engine", "sensor_schema_versions", ["engine"])
    op.create_index(
        "ux_sensor_schema_engine_version",
        "sensor_schema_versions",
        ["engine", "version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_sensor_schema_engine_version", table_name="sensor_schema_versions")
    op.drop_index("ix_sensor_schema_versions_engine", table_name="sensor_schema_versions")
    op.drop_table("sensor_schema_versions")

    op.drop_index("ix_solver_runs_type_started", table_name="solver_runs")
    op.drop_index("ix_solver_runs_name_started", table_name="solver_runs")
    op.drop_index("ix_solver_runs_task_id", table_name="solver_runs")
    op.drop_index("ix_solver_runs_error_code", table_name="solver_runs")
    op.drop_index("ix_solver_runs_task_type", table_name="solver_runs")
    op.drop_index("ix_solver_runs_solver_name", table_name="solver_runs")
    op.drop_table("solver_runs")

    op.drop_index("ix_task_logs_task_ts", table_name="task_logs")
    op.drop_index("ix_task_logs_task_id", table_name="task_logs")
    op.drop_table("task_logs")

    op.drop_index("ix_tasks_type_status", table_name="tasks")
    op.drop_index("ix_tasks_status_created", table_name="tasks")
    op.drop_index("ix_tasks_account_created", table_name="tasks")
    op.drop_index("ix_tasks_created_at", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_type", table_name="tasks")
    op.drop_index("ix_tasks_account_id", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ix_accounts_api_key", table_name="accounts")
    op.drop_table("accounts")
