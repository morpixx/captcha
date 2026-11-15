from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base. All models inherit from this."""


# ---------- accounts ----------


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: Plaintext key is never stored: populated only by pre-0004 rows until the
    #: migration nulls them. Lookups go through `api_key_hash`.
    api_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: HMAC-SHA256(pepper, key), hex. Unique so one key belongs to one account.
    api_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    #: Last four characters, for display only.
    key_tail: Mapped[str] = mapped_column(String(8), nullable=False, default="", server_default="")
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"), server_default="0")
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    tasks: Mapped[list[Task]] = relationship(back_populates="account", lazy="raise")


# Contract exports a `Balance` symbol; keep it as a thin typed alias onto Account.
# A full ledger table is a follow-up — for now balance is a single Numeric on Account.
Balance = Account


# ---------- tasks ----------


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending", index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"), server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Client verdict: -1 incorrect, 1 correct, NULL never reported.
    reported: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # 2captcha pingback target; set at create time when the client asks for one.
    callback_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    account: Mapped[Account] = relationship(back_populates="tasks", lazy="raise")
    logs: Mapped[list[TaskLog]] = relationship(back_populates="task", cascade="all, delete-orphan", lazy="raise")

    __table_args__ = (
        Index("ix_tasks_account_created", "account_id", "created_at"),
        Index("ix_tasks_status_created", "status", "created_at"),
        Index("ix_tasks_type_status", "type", "status"),
    )


class TaskLog(Base):
    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    task: Mapped[Task] = relationship(back_populates="logs", lazy="raise")

    __table_args__ = (Index("ix_task_logs_task_ts", "task_id", "ts"),)


# ---------- solver runs ----------


class SolverRun(Base):
    __tablename__ = "solver_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    solver_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        Index("ix_solver_runs_name_started", "solver_name", "started_at"),
        Index("ix_solver_runs_type_started", "task_type", "started_at"),
    )


# ---------- sensor schema drift ----------


# ---------- money ----------


class Transaction(Base):
    """Append-only ledger. `accounts.balance` is the running total; this is history.

    Every balance change writes a row here, so a client can always answer "where
    did this number come from" — which is the one thing a bare Numeric cannot.
    """

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # topup | charge | refund | bonus
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (Index("ix_transactions_account_created", "account_id", "created_at"),)


class PaymentIntent(Base):
    """A pending top-up. The provider id lives in `external_id`, provider data in `payload`.

    `status` moves pending -> paid exactly once, guarded by a conditional UPDATE,
    so a replayed webhook cannot credit the same payment twice.
    """

    __tablename__ = "payment_intents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_payment_intents_account_created", "account_id", "created_at"),)


class SensorSchemaVersion(Base):
    """Per-engine sensor-schema fingerprint. One row per (engine, version)."""

    __tablename__ = "sensor_schema_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    engine: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (Index("ux_sensor_schema_engine_version", "engine", "version", unique=True),)
