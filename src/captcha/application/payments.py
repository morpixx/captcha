"""Payment providers — the seam where a real acquirer plugs in.

The service never talks to an acquirer directly from a route: a route creates a
`PaymentIntent` row and asks a provider to start the payment. The provider
returns whatever the frontend needs (a redirect URL, an invoice, plain
instructions), and the money lands when the provider's webhook calls
`confirm` — a conditional UPDATE on `status = 'pending'`.

Adding YooKassa/Stripe/CryptoBot means writing one class here and registering it;
nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PaymentInstructions:
    """What the client gets back after asking for a top-up."""

    intent_id: str
    provider: str
    amount: str
    status: str
    payment_url: str | None = None
    instructions: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "intentId": self.intent_id,
            "provider": self.provider,
            "amount": self.amount,
            "status": self.status,
            "paymentUrl": self.payment_url,
            "instructions": self.instructions,
        }


class PaymentProvider(Protocol):
    """Start a payment for an already-created intent."""

    name: str

    async def start(
        self,
        *,
        intent_id: str,
        account_id: str,
        amount: Decimal,
        return_url: str | None = None,
    ) -> PaymentInstructions: ...


class ManualProvider:
    """Default provider: no acquirer. Credits only after an explicit confirmation.

    That confirmation is the `/account/topup/{id}/confirm` endpoint, guarded by
    the admin token — so a real acquirer's webhook can call the same code path
    once it exists.
    """

    name = "manual"

    async def start(
        self,
        *,
        intent_id: str,
        account_id: str,
        amount: Decimal,
        return_url: str | None = None,
    ) -> PaymentInstructions:
        return PaymentInstructions(
            intent_id=intent_id,
            provider=self.name,
            amount=f"{amount:.6f}",
            status="pending",
            payment_url=None,
            instructions=(
                f"Оплата {amount} для аккаунта {account_id}. "
                f"После поступления подтвердите: POST /account/topup/{intent_id}/confirm с X-Admin-Token."
            ),
        )


_PROVIDERS: dict[str, PaymentProvider] = {ManualProvider.name: ManualProvider()}


def register_provider(provider: PaymentProvider) -> None:
    _PROVIDERS[provider.name] = provider


def get_provider(name: str = "manual") -> PaymentProvider:
    try:
        return _PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(f"unknown payment provider: {name}") from exc


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)
