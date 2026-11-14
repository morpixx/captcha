# captcha-db

Postgres layer. SQLAlchemy 2.0 async, asyncpg driver, Alembic migrations.

Models: `Account`, `Task`, `TaskLog` (append-only), `SolverRun`, `SensorSchemaVersion`.

## Usage

```python
from captcha_db import init_engine, async_session, TaskRepo

init_engine("postgresql+asyncpg://captcha:captcha@localhost:5432/captcha", pool_size=20)

async with async_session() as s:
    repo = TaskRepo(s)
    task = await repo.create(account_id=..., type_="recaptcha_v2", payload={...})
```

FastAPI:

```python
from fastapi import Depends
from captcha_db import get_session

@app.post("/task")
async def create(s = Depends(get_session)):
    ...
```

## Migrations

```
cd packages/db
uv run alembic upgrade head
```

New revision:

```
uv run alembic revision -m "add foo"
```

`alembic.ini` lives at `packages/db/alembic.ini`. It reads `POSTGRES_URL` from env; falls back to the Settings default.

## Notes

- Primary keys are `uuid` (Postgres native). Timestamps are `TIMESTAMPTZ`.
- Money fields use `Numeric(18, 6)` — six-decimal precision, room for sub-cent accounting.
- `Task.payload`, `Task.result`, `Task.error`, `TaskLog.payload` are `JSONB`.
- Hot indexes: `Task(account_id, created_at desc)`, `Task(status, created_at)`, `TaskLog(task_id, ts)`, `SolverRun(solver_name, started_at desc)`.
