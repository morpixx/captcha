from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from captcha.domain.errors import ConfigError, UnsolvableError

from .models import Account, Task, TaskLog


class AccountRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, account_id: uuid.UUID) -> Account | None:
        return await self.s.get(Account, account_id)

    async def get_by_key(self, api_key: str) -> Account | None:
        stmt = select(Account).where(Account.api_key == api_key).limit(1)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def create(self, *, api_key: str, name: str = "", balance: Decimal | None = None) -> Account:
        acc = Account(api_key=api_key, name=name, balance=balance or Decimal("0"))
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
        return Decimal(row[0])

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
        return Decimal(row[0])


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
    ) -> Task:
        t = Task(
            account_id=account_id,
            type=type_,
            status="pending",
            payload=payload,
            cost=cost or Decimal("0"),
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

    async def bump_retry(self, task_id: uuid.UUID) -> int:
        stmt = update(Task).where(Task.id == task_id).values(retries=Task.retries + 1).returning(Task.retries)
        row = (await self.s.execute(stmt)).first()
        return int(row[0]) if row else 0
