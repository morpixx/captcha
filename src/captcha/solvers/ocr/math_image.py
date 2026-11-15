"""math_image — OCR a math-expression captcha and evaluate it."""

from __future__ import annotations

import re
import time
from typing import ClassVar

from simpleeval import InvalidExpression, NumberTooHigh, SimpleEval

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ModelInferenceError
from captcha.solvers.base import Solver, SolverContext, SolverInfo
from captcha.solvers.ocr.image_to_text import ImageToTextSolver

# charset normalizer: common OCR confusions in handwritten/distorted math captchas
_CHAR_MAP = {
    "×": "*",  # ×
    "x": "*",
    "X": "*",
    "⋅": "*",  # ⋅
    "·": "*",  # ·
    "÷": "/",  # ÷
    "−": "-",  # −
    "–": "-",  # –
    "—": "-",  # —
    "=": "",
    "?": "",
    "…": "",
    "O": "0",
    "o": "0",
    "l": "1",
    "I": "1",
    "S": "5",
    "s": "5",
    "B": "8",
    "Z": "2",
    "z": "2",
    " ": "",
}

_VALID_RE = re.compile(r"^[\d+\-*/().]+$")


def _sanitize(raw: str) -> str:
    """Normalise OCR noise into a math expression, or return "" if there is none.

    Deliberately strict: a stray letter that is not a known OCR confusion means the
    model read garbage, and answering with a number made of leftover digits is worse
    than failing the task. The caller turns "" into ModelInferenceError.
    """
    cleaned = "".join(_CHAR_MAP.get(ch, ch) for ch in raw).strip()
    if not cleaned or not _VALID_RE.match(cleaned):
        return ""
    return cleaned


class MathImageSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="math_image",
        types=(CaptchaType.MATH_IMAGE,),
        http=True,

    )

    def __init__(self) -> None:
        super().__init__()
        self._ocr = ImageToTextSolver()
        self._eval = SimpleEval(functions={})
        self._eval.names = {}

    async def prepare(self, ctx: SolverContext) -> None:
        await self._ocr.prepare(ctx)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.perf_counter()
        # forward to image_to_text; force case-insensitive upstream shouldn't matter — digits only
        ocr_result = await self._ocr.solve_http(task, ctx)
        raw = (ocr_result.text or "").strip()
        if not raw:
            raise ModelInferenceError("math_image: OCR returned empty string")

        expr = _sanitize(raw)
        if not expr or not _VALID_RE.match(expr):
            raise ModelInferenceError(
                f"math_image: cannot sanitize OCR output to a math expression (raw={raw!r})",
                context={"raw": raw, "sanitized": expr},
            )

        try:
            value = self._eval.eval(expr)
        except (InvalidExpression, NumberTooHigh, SyntaxError, ZeroDivisionError, ValueError) as e:
            raise ModelInferenceError(
                f"math_image: eval failed on {expr!r} ({e})",
                context={"expr": expr, "raw": raw},
            ) from e

        text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)

        return SolverResult(
            text=text,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            extra={"raw_ocr": raw, "expr": expr},
        )

    async def shutdown(self) -> None:
        await self._ocr.shutdown()
