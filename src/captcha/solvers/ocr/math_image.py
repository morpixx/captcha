"""math_image - OCR a math-expression captcha and evaluate it.

The OCR half is image_to_text (PaddleOCR/TrOCR); this module only normalises the
recognised string and evaluates it with simpleeval. An expression that cannot be
reduced to arithmetic is a failed task, not a guess: a wrong digit string is
scored as a wrong answer and costs the same as an honest model error.
"""

from __future__ import annotations

import math
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
_MAX_EXPR_CHARS = 64


def _sanitize(raw: str) -> str:
    """Normalise OCR noise into a math expression, or return "" if there is none.

    Deliberately strict: a stray letter that is not a known OCR confusion means the
    model read garbage, and answering with a number made of leftover digits is worse
    than failing the task. The caller turns "" into ModelInferenceError.
    """
    cleaned = "".join(_CHAR_MAP.get(ch, ch) for ch in raw).strip()
    if not cleaned or len(cleaned) > _MAX_EXPR_CHARS or not _VALID_RE.match(cleaned):
        return ""
    # simpleeval allows exponentiation; no captcha needs it and 9**9**9 is not a
    # math puzzle anybody wants us to compute on a worker.
    if "**" in cleaned:
        return ""
    return cleaned


class MathImageSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="math_image",
        types=(CaptchaType.MATH_IMAGE,),
        http=True,
        version="0.2.0",
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
        ocr_result = await self._ocr.solve_http(task, ctx)
        raw = (ocr_result.text or "").strip()
        if not raw:
            raise ModelInferenceError("math_image: OCR returned empty string")

        expr = _sanitize(raw)
        if not expr:
            raise ModelInferenceError(
                f"math_image: cannot sanitize OCR output to a math expression (raw={raw!r})",
                context={"raw": raw, "sanitized": expr},
            )

        try:
            value = self._eval.eval(expr)
        except (InvalidExpression, NumberTooHigh, SyntaxError, ZeroDivisionError, ValueError) as exc:
            raise ModelInferenceError(
                f"math_image: eval failed on {expr!r} ({exc})",
                context={"expr": expr, "raw": raw},
            ) from exc
        except (OverflowError, MemoryError, RecursionError) as exc:
            raise ModelInferenceError(
                f"math_image: expression {expr!r} exceeded evaluation limits",
                context={"expr": expr},
            ) from exc

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ModelInferenceError(
                f"math_image: expression {expr!r} did not evaluate to a number",
                context={"expr": expr, "type": type(value).__name__},
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise ModelInferenceError(f"math_image: non-finite result for {expr!r}", context={"expr": expr})

        text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)

        return SolverResult(
            text=text,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            extra={"raw_ocr": raw, "expr": expr},
        )

    async def shutdown(self) -> None:
        await self._ocr.shutdown()
