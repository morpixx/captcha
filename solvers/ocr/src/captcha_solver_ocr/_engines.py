"""Lazy OCR engine singletons. Import on first use so the package stays importable
without heavyweight ML deps installed."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from captcha_common.errors import ModelInferenceError

if TYPE_CHECKING:
    from PIL.Image import Image

_PADDLE_LOCK = threading.Lock()
_TROCR_LOCK = threading.Lock()
_paddle_instance: Any = None
_trocr_instance: tuple[Any, Any, str] | None = None  # (processor, model, device)


_PADDLE_HINT = (
    "PaddleOCR is not installed. Install the engine extra: "
    "`uv pip install -e solvers/ocr[paddle]` "
    "(pulls paddlepaddle + paddleocr)."
)
_TROCR_HINT = (
    "TrOCR deps missing. Install the engine extra: "
    "`uv pip install -e solvers/ocr[trocr]` "
    "(pulls transformers + torch)."
)


def get_paddle(lang: str = "en") -> Any:
    """Return a cached PaddleOCR instance for the requested language.

    First call imports paddleocr and warms the pipeline — expensive, hence cached.
    Subsequent calls with a different language rebuild. Raises ModelInferenceError
    with an install note on ImportError.
    """
    global _paddle_instance
    with _PADDLE_LOCK:
        if _paddle_instance is not None and getattr(_paddle_instance, "_captcha_lang", None) == lang:
            return _paddle_instance
        try:
            from paddleocr import PaddleOCR  # type: ignore[import-not-found]
        except Exception as e:  # pragma: no cover - import guard
            raise ModelInferenceError(_PADDLE_HINT, context={"import_error": repr(e)}) from e
        try:
            ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
        except TypeError:
            # newer paddleocr dropped show_log
            ocr = PaddleOCR(use_angle_cls=True, lang=lang)
        ocr._captcha_lang = lang  # type: ignore[attr-defined]
        _paddle_instance = ocr
        return ocr


def get_trocr(device: str = "cpu", model_name: str = "microsoft/trocr-base-printed") -> tuple[Any, Any, str]:
    """Return (processor, model, device). Cached per device."""
    global _trocr_instance
    with _TROCR_LOCK:
        if _trocr_instance is not None and _trocr_instance[2] == device:
            return _trocr_instance
        try:
            import torch  # type: ignore[import-not-found]
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel  # type: ignore[import-not-found]
        except Exception as e:  # pragma: no cover
            raise ModelInferenceError(_TROCR_HINT, context={"import_error": repr(e)}) from e
        try:
            processor = TrOCRProcessor.from_pretrained(model_name)
            model = VisionEncoderDecoderModel.from_pretrained(model_name)
            model = model.to(device)
            model.eval()
            # keep gradients off
            torch.set_grad_enabled(False)
        except Exception as e:
            raise ModelInferenceError(
                f"failed to load TrOCR model '{model_name}': {e}",
                context={"model": model_name, "device": device},
            ) from e
        _trocr_instance = (processor, model, device)
        return _trocr_instance


def paddle_read(image: Image, lang: str = "en") -> str:
    """Run PaddleOCR on a PIL image and return concatenated text."""
    import numpy as np  # local import; numpy is a paddle dep

    ocr = get_paddle(lang)
    arr = np.array(image.convert("RGB"))
    try:
        result = ocr.ocr(arr, cls=True)
    except Exception as e:
        raise ModelInferenceError(f"PaddleOCR failed: {e}", context={"lang": lang}) from e
    if not result:
        return ""
    # paddleocr returns list[list[[box, (text, conf)]]]; shape varies by version
    pieces: list[str] = []
    for page in result:
        if not page:
            continue
        for line in page:
            try:
                text = line[1][0]
            except (IndexError, TypeError):
                continue
            if text:
                pieces.append(str(text))
    return " ".join(pieces).strip()


def trocr_read(image: Image, device: str = "cpu") -> str:
    """Run TrOCR on a PIL image and return decoded text."""
    import torch  # type: ignore[import-not-found]

    processor, model, _ = get_trocr(device)
    try:
        pixel_values = processor(images=image.convert("RGB"), return_tensors="pt").pixel_values.to(device)
        with torch.inference_mode():
            generated_ids = model.generate(pixel_values)
        text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
    except Exception as e:
        raise ModelInferenceError(f"TrOCR inference failed: {e}", context={"device": device}) from e
    return str(text).strip()
