"""One Arkose session's rounds: classify the game, delegate, submit, repeat.

Arkose can chain several rounds inside one session (submit one image set, get the
next). The loop lives here so funcaptcha.py stays a thin transport shell: it owns
the token, we own the answers.
"""

from __future__ import annotations

from typing import Any

from captcha.domain import SolveMode, SolverResult, Task
from captcha.domain.errors import (
    ChallengeExpiredError,
    NeedsBrowserError,
    SensorError,
    UnsolvableError,
)
from captcha.solvers import SolverContext

from . import settings, variants, wire

MAX_ROUNDS = 5


async def play(
    *,
    task: Task,
    ctx: SolverContext,
    cfg: settings.ArkoseSettings,
    payload: dict[str, Any],
    token: str,
    user_agent: str,
    challenge: dict[str, Any],
    bda_source: str,
) -> SolverResult:
    """Answer rounds until Arkose accepts a token or the budget is spent."""
    registry = ctx.extra.get("solver_registry") if isinstance(ctx.extra, dict) else None
    game_token = game_token_of(challenge)
    variant = ""
    for round_index in range(1, MAX_ROUNDS + 1):
        variant = variants.classify(challenge, payload)
        if variant == "unsupported":
            raise NeedsBrowserError(
                "funcaptcha game is not modelled in-process",
                context={
                    "game_type": variants.game_type_of(challenge, payload),
                    "round": round_index,
                    "surl": cfg.surl,
                },
            )
        answer = await _answer(registry, task, ctx, challenge=challenge, payload=payload, variant=variant)

        reply = await wire.submit_answer(
            ctx,
            cfg,
            task,
            session_token=token,
            game_token=game_token,
            guess=wire.encode_guess(answer, challenge=challenge, ctx=ctx),
            bio=str(payload.get("bio") or ""),
            user_agent=user_agent,
        )
        if wire.solved(reply):
            return result(
                str(reply.get("token") or token),
                user_agent,
                path=variant,
                extra={
                    "bda_source": bda_source,
                    "variant": variant,
                    "rounds": round_index,
                    "game_type": variants.game_type_of(challenge, payload),
                },
            )

        images = wire.next_round_images(reply)
        if not images:
            raise ChallengeExpiredError(
                "arkose rejected the answer and asked no follow-up round",
                context={"variant": variant, "round": round_index, "reply_keys": sorted(reply)},
            )
        challenge = dict(reply)
        challenge["_challenge_imgs"] = images
        game_token = game_token_of(challenge)

    raise UnsolvableError(
        "funcaptcha round budget exhausted",
        context={"rounds": MAX_ROUNDS, "variant": variant},
    )


async def _answer(
    registry: object,
    task: Task,
    ctx: SolverContext,
    *,
    challenge: dict[str, Any],
    payload: dict[str, Any],
    variant: str,
) -> list[Any]:
    """Run the variant's solver; a capability gap is an escalation, not a retry."""
    try:
        outcome = await variants.dispatch(
            registry,
            task,
            ctx,
            variant=variant,
            challenge=challenge,
            payload=payload,
        )
        return variants.answer_for(outcome, variant)
    except UnsolvableError as exc:
        raise NeedsBrowserError(
            "funcaptcha variant solver could not answer this round",
            context={"variant": variant, "cause": exc.message, "game_type": variants.game_type_of(challenge, payload)},
        ) from exc


def game_token_of(challenge: dict[str, Any]) -> str:
    value = challenge.get("challengeID") or challenge.get("challenge_id") or challenge.get("game_token")
    if not isinstance(value, str) or not value:
        raise SensorError(
            "arkose challenge carries no challengeID",
            context={"keys": sorted(challenge)},
        )
    return value


def result(token: str, user_agent: str, *, path: str, extra: dict[str, Any]) -> SolverResult:
    """2captcha FunCaptcha shape: token plus the UA that minted it."""
    merged: dict[str, Any] = {"userAgent": user_agent, "path": path}
    merged.update(extra)
    return SolverResult(token=token, solve_mode=SolveMode.HTTP, extra=merged)
