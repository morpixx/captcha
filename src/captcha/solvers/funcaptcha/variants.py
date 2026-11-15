"""Arkose game variants -> the solver that can actually answer them.

One Arkose session carries one game type, and the games split into three shapes:

    rotate  -> a single angle            (geometry: solvers/rotate)
    grid    -> a 2x3 tile pick           (classification head: funcaptcha_grid)
    match   -> pick the pair / odd tile  (the same head, prompt-conditioned)

A suppressed session (token carries sup=1) never asks a question at all and is
answered by the token endpoint directly.

Game-type names come from public dumps; Arkose renames games, so the fallback is
by shape, not by name: a challenge with images plus an instruction is a grid
round. Everything else is UnsolvableError with the observed name in context —
never a guess.
"""

from __future__ import annotations

import contextlib

import msgspec

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ConfigError, UnsolvableError
from captcha.solvers import Solver, SolverContext

ROTATE_GAMES = frozenset({"rotate", "rollball", "3d_rollball", "3d_rollball_animals", "3d_rollball_objects"})
GRID_GAMES = frozenset(
    {
        "image_label_binary",
        "image_verification",
        "tiles",
        "tile_match",
        "rockstack",
        "spatial_lock",
        "card_animals",
    }
)
MATCH_GAMES = frozenset(
    {
        "match",
        "matching",
        "match_key",
        "matching_game",
        "image_matching",
        "hand_number",
        "handnumber",
        "cardistry",
    }
)
DEFAULT_HEAD = "funcap-v1"
DEFAULT_ROWS = 2
DEFAULT_COLS = 3


def game_type_of(challenge: dict, payload: dict) -> str:
    for source in (challenge, payload):
        for key in ("game_type", "gameType", "game"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def classify(challenge: dict, payload: dict) -> str:
    """Variant name: rotate / grid / match / suppressed / unsupported."""
    override = str(payload.get("variant") or "").strip().lower()
    if override in ("rotate", "grid", "match"):
        return override

    name = game_type_of(challenge, payload).lower()
    if name in ROTATE_GAMES:
        return "rotate"
    if name in GRID_GAMES:
        return "grid"
    if name in MATCH_GAMES:
        return "match"
    if challenge_images(challenge) and instruction_of(challenge, payload):
        return "grid"
    return "unsupported"


def challenge_images(challenge: dict) -> list[str]:
    for key in ("_challenge_imgs", "challenge_imgs", "_challenge_imgs_2"):
        value = challenge.get(key)
        if isinstance(value, list):
            images = [item for item in value if isinstance(item, str) and item]
            if images:
                return images
    return []


def instruction_of(challenge: dict, payload: dict) -> str:
    for source in (challenge, payload):
        for key in ("instruction", "instructions", "question", "prompt"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


async def dispatch(
    registry: object,
    task: Task,
    ctx: SolverContext,
    *,
    variant: str,
    challenge: dict,
    payload: dict,
) -> SolverResult:
    """Run the sibling solver that owns this variant and hand back its answer."""
    if variant in ("grid", "match"):
        solver_cls = _resolve(registry, preferred=("funcaptcha_grid",), captcha_type=CaptchaType.CLASSIFICATION_GRID)
        sub_payload = _grid_payload(challenge, payload)
        sub_type = CaptchaType.CLASSIFICATION_GRID
    elif variant == "rotate":
        solver_cls = _resolve(registry, preferred=("funcaptcha_rotate", "rotate"), captcha_type=CaptchaType.ROTATE)
        sub_payload = _rotate_payload(challenge, payload)
        sub_type = CaptchaType.ROTATE
    else:
        raise UnsolvableError(
            "funcaptcha variant is not answerable",
            context={"variant": variant, "game_type": game_type_of(challenge, payload)},
        )

    solver: Solver = solver_cls()
    sub_task = msgspec.structs.replace(
        task,
        type=sub_type,
        payload=sub_payload,
        preferred_mode=SolveMode.HTTP,
    )
    try:
        await solver.prepare(ctx)
        return await solver.solve_http(sub_task, ctx)
    finally:
        # shutdown must not mask the answer the solver already produced
        with contextlib.suppress(Exception):
            await solver.shutdown()


def answer_for(result: SolverResult, variant: str) -> list[object]:
    """SolverResult -> the guess list Arkose expects for this variant."""
    if variant in ("grid", "match"):
        if not result.indices:
            raise UnsolvableError("grid solver returned no tiles", context={"variant": variant})
        return [int(index) for index in result.indices]
    if result.angle is None:
        raise UnsolvableError("rotate solver returned no angle", context={"variant": variant})
    return [round(result.angle) % 360]


def _grid_payload(challenge: dict, payload: dict) -> dict:
    images = challenge_images(challenge)
    if not images:
        raise UnsolvableError(
            "funcaptcha challenge has no images for a grid round",
            context={"keys": sorted(challenge)},
        )
    prompt = instruction_of(challenge, payload)
    if not prompt:
        raise UnsolvableError("funcaptcha grid round has no instruction", context={"keys": sorted(challenge)})
    return {
        "image_b64": images[0],
        "prompt": prompt,
        "rows": int(payload.get("rows") or DEFAULT_ROWS),
        "cols": int(payload.get("cols") or DEFAULT_COLS),
        "head": str(payload.get("head") or payload.get("match_head") or DEFAULT_HEAD),
        # Extra frames travel along for heads that want the whole set; the grid
        # classifier ignores unknown keys.
        "extra_images": images[1:],
        "challenge_id": str(challenge.get("challengeID") or challenge.get("challenge_id") or ""),
    }


def _rotate_payload(challenge: dict, payload: dict) -> dict:
    images = challenge_images(challenge)
    image = images[0] if images else payload.get("image")
    if not isinstance(image, str) or not image:
        raise UnsolvableError(
            "funcaptcha rotate round has no image",
            context={"keys": sorted(challenge)},
        )
    return {
        "image": image,
        "target_angle": payload.get("target_angle"),
        "challenge_id": str(challenge.get("challengeID") or challenge.get("challenge_id") or ""),
    }


def _resolve(registry: object, *, preferred: tuple[str, ...], captcha_type: CaptchaType) -> type[Solver]:
    if registry is None:
        raise ConfigError(
            "funcaptcha needs ctx.extra['solver_registry'] to reach its variant solver",
            context={"type": captcha_type.value},
        )
    by_name = _by_name(registry, preferred)
    if by_name is not None:
        return by_name
    pick = getattr(registry, "pick", None)
    if pick is None:
        raise ConfigError("solver registry exposes no pick()", context={"type": captcha_type.value})
    found = pick(captcha_type, allow_experimental=True)
    if found is None:
        raise UnsolvableError(
            "no solver registered for the funcaptcha variant",
            context={"type": captcha_type.value, "wanted": list(preferred)},
        )
    return found


def _by_name(registry: object, names: tuple[str, ...]) -> type[Solver] | None:
    all_solvers = getattr(registry, "all_solvers", None)
    if all_solvers is None:
        return None
    for solver_cls in all_solvers():
        info = getattr(solver_cls, "info", None)
        if info is not None and getattr(info, "name", "") in names:
            return solver_cls
    return None
