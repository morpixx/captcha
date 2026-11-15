"""security: hashed API keys (api_key_hash + key_tail)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-15 00:00:00

Backfills every existing plaintext key into HMAC-SHA256(pepper, key), keeps the
last four characters in key_tail, then nulls the plaintext column. This is an
online data migration: `alembic upgrade --sql` cannot render it because it
reads rows, which is the price of hashing keys that already exist.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from captcha.adapters.storage.repos import api_key_pepper
from captcha.domain.apikey import hash_api_key, key_tail

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _text(value: object) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def upgrade() -> None:
    op.add_column("accounts", sa.Column("api_key_hash", sa.String(length=64), nullable=True))
    op.add_column("accounts", sa.Column("key_tail", sa.String(length=8), nullable=False, server_default=""))
    # The plaintext column stays only so old rows can be read during the
    # backfill; it is nulled below and never written again.
    op.alter_column("accounts", "api_key", existing_type=sa.String(length=64), nullable=True)
    op.drop_index("ix_accounts_api_key", table_name="accounts")
    op.drop_constraint("uq_accounts_api_key", "accounts", type_="unique")

    conn = op.get_bind()
    pepper = api_key_pepper()
    rows = conn.execute(sa.text("SELECT id, api_key FROM accounts WHERE api_key IS NOT NULL AND api_key <> ''")).all()
    for account_id, raw_key in rows:
        key = _text(raw_key)
        conn.execute(
            sa.text(
                "UPDATE accounts SET api_key_hash = :key_hash, key_tail = :tail, api_key = NULL WHERE id = :account_id"
            ),
            {"key_hash": hash_api_key(key, pepper), "tail": key_tail(key), "account_id": account_id},
        )

    op.create_index("ix_accounts_api_key_hash", "accounts", ["api_key_hash"], unique=True)


def downgrade() -> None:
    # Data-level downgrade is impossible: the plaintext keys are gone. The
    # schema goes back to the 0003 shape, api_key stays nullable and empty.
    op.drop_index("ix_accounts_api_key_hash", table_name="accounts")
    op.create_index("ix_accounts_api_key", "accounts", ["api_key"], unique=True)
    op.create_unique_constraint("uq_accounts_api_key", "accounts", ["api_key"])
    op.drop_column("accounts", "key_tail")
    op.drop_column("accounts", "api_key_hash")
