"""reporting verdict + pingback callback url

Revision ID: 0002
Revises: 0001_init
Create Date: 2026-11-15 00:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("reported", sa.SmallInteger(), nullable=True))
    op.add_column("tasks", sa.Column("callback_url", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "callback_url")
    op.drop_column("tasks", "reported")
