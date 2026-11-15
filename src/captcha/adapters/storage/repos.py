from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from captcha.domain.apikey import hash_api_key, key_tail
from captcha.domain.errors import ConfigError, UnsolvableError

from .models import Account, PaymentIntent, Task, TaskLog, Transaction


def api_key_pepper() -> str:
    """Pepper for key hashing, read through Settings.

    Settings is the only reader of .env, so this is what keeps the CLI (which
    mints keys), the gateway (which verifies them) and the 0004 backfill
    deriving the same hash from the same configuration.
    """
    from captcha.config import get_settings

    return get_settings().api_key_pepper


class AccountRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, account_id: uuid.UUID) -> Account | None:
        return await self.s.get(Account, account_id)

    async def get_by_key(self, api_key: str) -> Account | None:
        """Look the account up by hash. Plaintext never reaches this query."""
        if not api_key:
            return None
        stmt = select(Account).where(Account.api_key_hash == hash_api_key(api_key, api_key_pepper())).limit(1)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def create(self, *, api_key: str, name: str = "", balance: Decimal | None = None) -> Account:
        """Mint an account from a plaintext key.

        The key is hashed here and dropped: callers that need to show it to the
        operator (the CLI seed path) still hold the argument they passed in.
        """
        acc = Account(
            api_key=None,
            api_key_hash=hash_api_key(api_key, api_key_pepper()),
            key_tail=key_tail(api_key),
            name=name,
            balance=balance or Decimal("0"),
        )
        self.s.add(acc)
        await self.s.flush()
        return acc

    async def charge(self, account_id: uuid.UUID, amount: Decimal) -> Decimal:
        """Atomic debit. Raises UnsolvableError('ERR_BALANCE') on insufficient funds.

        Uses a conditional UPDATE so two workers can't double-spend; the RETURNING
        clause gives the new balance back in the same round trip.
        """
        if amount <= 0:
            raise ConfigError("charge amount must be positive")
        stmt = (
            update(Account)
            .where(Account.id == account_id, Account.balance >= amount, Account.disabled.is_(False))
            .values(balance=Account.balance - amount)
            .returning(Account.balance)
        )
        row = (await self.s.execute(stmt)).first()
        if row is None:
            err = UnsolvableError("insufficient balance", context={"account_id": str(account_id)})
            err.code = "ERR_BALANCE"
            raise err
        balance = Decimal(row[0])
        await self._ledger(account_id, "charge", amount, balance)
        return balance

    async def refund(self, account_id: uuid.UUID, amount: Decimal) -> Decimal:
        if amount <= 0:
            raise ConfigError("refund amount must be positive")
        stmt = (
            update(Account)
            .where(Account.id == account_id)
            .values(balance=Account.balance + amount)
            .returning(Account.balance)
        )
        row = (await self.s.execute(stmt)).first()
        if row is None:
            raise ConfigError(f"account {account_id} not found")
        balance = Decimal(row[0])
        await self._ledger(account_id, "refund", amount, balance)
        return balance

    async def _ledger(
        self,
        account_id: uuid.UUID,
        kind: str,
        amount: Decimal,
        balance_after: Decimal,
        *,
        task_id: uuid.UUID | None = None,
        external_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.s.add(
            Transaction(
                account_id=account_id,
                kind=kind,
                amount=amount,
                balance_after=balance_after,
                task_id=task_id,
                external_id=external_id,
                meta=meta,
            )
        )

    async def topup(
        self,
        account_id: uuid.UUID,
        amount: Decimal,
        *,
        external_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Decimal:
        """Credit the account and write the matching ledger row."""
        if amount <= 0:
            raise ConfigError("topup amount must be positive")
        stmt = update(Account).where(Account.id == account_id).values(balance=Account.balance + amount).returning(
            Account.balance
        )
        row = (await self.s.execute(stmt)).first()
        if row is None:
            raise ConfigError(f"account {account_id} not found")
        balance = Decimal(row[0])
        await self._ledger(account_id, "topup", amount, balance, external_id=external_id, meta=meta)
        return balance


class TaskRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self.s.get(Task, task_id)

    async def create(
        self,
        *,
        account_id: uuid.UUID,
        type_: str,
        payload: dict[str, Any],
        cost: Decimal | None = None,
        callback_url: str | None = None,
    ) -> Task:
        t = Task(
            account_id=account_id,
            type=type_,
            status="pending",
            payload=payload,
            cost=cost or Decimal("0"),
            callback_url=callback_url,
        )
        self.s.add(t)
        await self.s.flush()
        return t

    async def update_status(
        self,
        task_id: uuid.UUID,
        status: str,
        *,
        worker_id: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        now = datetime.now(UTC)
        if status == "processing":
            values["started_at"] = now
            if worker_id is not None:
                values["worker_id"] = worker_id
        elif status in ("ready", "failed"):
            values["finished_at"] = now
        await self.s.execute(update(Task).where(Task.id == task_id).values(**values))

    async def update_result(
        self,
        task_id: uuid.UUID,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        values: dict[str, Any] = {"finished_at": datetime.now(UTC)}
        if result is not None:
            values["result"] = result
            values["status"] = "ready"
        if error is not None:
            values["error"] = error
            values["status"] = "failed"
        await self.s.execute(update(Task).where(Task.id == task_id).values(**values))

    async def log(
        self,
        task_id: uuid.UUID,
        event: str,
        *,
        level: str = "INFO",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.s.add(TaskLog(task_id=task_id, event=event, level=level, payload=payload))

    async def report(self, task_id: uuid.UUID, correct: bool) -> None:
        """Store the client verdict: 1 for reportCorrect, -1 for reportIncorrect."""
        await self.s.execute(update(Task).where(Task.id == task_id).values(reported=1 if correct else -1))

    async def set_callback_url(self, task_id: uuid.UUID, callback_url: str | None) -> None:
        await self.s.execute(update(Task).where(Task.id == task_id).values(callback_url=callback_url))

    async def bump_retry(self, task_id: uuid.UUID) -> int:
        stmt = update(Task).where(Task.id == task_id).values(retries=Task.retries + 1).returning(Task.retries)
        row = (await self.s.execute(stmt)).first()
        return int(row[0]) if row else 0

    async def refund_once(self, task_id: uuid.UUID) -> Decimal | None:
        """Give the task's cost back to its account, exactly once.

        Zeroing `tasks.cost` is the idempotency marker: the WHERE clause only
        matches a task that still carries a charge, so a duplicate call (worker
        retry, crash after write) returns None and moves no money.
        """
        stmt = (
            update(Task)
            .where(Task.id == task_id, Task.cost > 0)
            .values(cost=Decimal("0"))
            .returning(Task.account_id, Task.cost)
        )
        row = (await self.s.execute(stmt)).first()
        if row is None:
            return None
        account_id, cost = row[0], Decimal(row[1])
        credited = (
            await self.s.execute(
                update(Account).where(Account.id == account_id).values(balance=Account.balance + cost).returning(Account.balance)
            )
        ).first()
        balance = Decimal(credited[0]) if credited else Decimal("0")
        self.s.add(
            Transaction(
                account_id=account_id,
                kind="refund",
                amount=cost,
                balance_after=balance,
                task_id=task_id,
            )
        )
        return cost


class UsageRepo:
    """Aggregates over `tasks` for one account. Two queries, no N+1."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def by_type(self, account_id: uuid.UUID, since: datetime) -> list[tuple[Any, ...]]:
        latency = func.avg(func.extract("epoch", Task.finished_at - Task.started_at) * 1000.0)
        stmt = (
            select(Task.type, Task.status, func.count(), latency, func.sum(Task.cost))
            .where(Task.account_id == account_id, Task.created_at >= since)
            .group_by(Task.type, Task.status)
        )
        return [tuple(row) for row in (await self.s.execute(stmt)).all()]

    async def by_day(self, account_id: uuid.UUID, since: datetime) -> list[tuple[Any, ...]]:
        day = func.date(Task.created_at)
        stmt = (
            select(day, func.count(), func.sum(Task.cost))
            .where(Task.account_id == account_id, Task.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
        return [tuple(row) for row in (await self.s.execute(stmt)).all()]


class TransactionRepo:
    """Read side of the ledger. Writes go through AccountRepo/TaskRepo."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def list_for_account(
        self,
        account_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Transaction]:
        stmt = (
            select(Transaction)
            .where(Transaction.account_id == account_id)
            .order_by(Transaction.created_at.desc(), Transaction.id.desc())
            .limit(max(1, min(limit, 200)))
            .offset(max(0, offset))
        )
        return list((await self.s.execute(stmt)).scalars().all())


class PaymentIntentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def create(
        self,
        *,
        account_id: uuid.UUID,
        amount: Decimal,
        provider: str,
        payload: dict[str, Any] | None = None,
    ) -> PaymentIntent:
        intent = PaymentIntent(account_id=account_id, amount=amount, provider=provider, payload=payload)
        self.s.add(intent)
        await self.s.flush()
        return intent

    async def get(self, intent_id: uuid.UUID) -> PaymentIntent | None:
        return await self.s.get(PaymentIntent, intent_id)

    async def claim(self, intent_id: uuid.UUID, *, external_id: str | None = None) -> PaymentIntent | None:
        """Move pending -> paid, exactly once. Returns None if it was not pending.

        The conditional UPDATE is the idempotency guard: a replayed webhook finds
        no pending row and must not credit the account again.
        """
        stmt = (
            update(PaymentIntent)
            .where(PaymentIntent.id == intent_id, PaymentIntent.status == "pending")
            .values(status="paid", paid_at=datetime.now(UTC), external_id=external_id)
            .returning(PaymentIntent.id, PaymentIntent.account_id, PaymentIntent.amount)
        )
        row = (await self.s.execute(stmt)).first()
        if row is None:
            return None
        return await self.s.get(PaymentIntent, row[0])


class SolverRunRepo:
    """One row per solve attempt. The only place per-solver latency is kept."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def record(
        self,
        *,
        solver_name: str,
        task_type: str,
        ok: bool,
        latency_ms: float | None = None,
        error_code: str | None = None,
        task_id: uuid.UUID | None = None,
    ) -> None:
        from .models import SolverRun

        self.s.add(
            SolverRun(
                solver_name=solver_name,
                task_type=task_type,
                ok=ok,
                latency_ms=int(latency_ms) if latency_ms is not None else None,
                error_code=error_code,
                task_id=task_id,
            )
        )
