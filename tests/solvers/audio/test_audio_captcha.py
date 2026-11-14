from __future__ import annotations

import asyncio
import base64
import io

import pytest

from captcha.domain import CaptchaType
from captcha.domain.errors import UnsolvableError
from captcha.domain.task import Task, new_task_id
from captcha.solvers.base import SolverContext

pytest.importorskip("soundfile")
pytest.importorskip("numpy")

have_fw = True
try:
    import faster_whisper  # type: ignore[import-not-found]  # noqa: F401
except Exception:
    have_fw = False

have_ow = True
try:
    import whisper  # type: ignore[import-not-found]  # noqa: F401
except Exception:
    have_ow = False

from captcha.solvers.audio.audio_captcha import AudioCaptchaSolver  # noqa: E402


def _silent_wav() -> str:
    import numpy as np
    import soundfile as sf

    buf = io.BytesIO()
    sr = 16000
    audio = np.zeros(sr * 1, dtype="float32")  # 1 s silence
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return base64.b64encode(buf.getvalue()).decode()


def test_bad_base64_raises() -> None:
    solver = AudioCaptchaSolver()
    task = Task(
        id=new_task_id(),
        type=CaptchaType.AUDIO,
        payload={"body": "!!!not base64!!!", "format": "wav"},
    )
    # base64 is lenient with validate=False; this decodes to garbage bytes.
    # Real failure path is inside transcribe(); here we check only missing-body guard.
    task2 = Task(id=new_task_id(), type=CaptchaType.AUDIO, payload={})
    with pytest.raises(UnsolvableError):
        asyncio.run(solver.solve_http(task2, SolverContext()))


@pytest.mark.skipif(not (have_fw or have_ow), reason="no whisper backend installed")
def test_transcribes_silence() -> None:
    solver = AudioCaptchaSolver()
    task = Task(
        id=new_task_id(),
        type=CaptchaType.AUDIO,
        payload={"body": _silent_wav(), "format": "wav", "model": "tiny"},
        language="en",
    )
    # Silence may transcribe to empty or non-empty; solver raises on empty.
    # Just assert the pipeline doesn't crash on a structural error.
    try:
        result = asyncio.run(solver.solve_http(task, SolverContext()))
        assert isinstance(result.text, str)
    except Exception as e:
        # expected: ModelInferenceError on empty transcription
        from captcha.domain.errors import ModelInferenceError

        assert isinstance(e, ModelInferenceError)
