"""Lazy OCR engine singletons.

Both engines are optional. The base worker image ships neither paddlepaddle nor
torch/transformers, so imports happen on first use and stay behind guards.

Error split:
  * runtime/engine not installed          -> ConfigError (ERR_CONFIG, terminal)
  * local weights missing or incomplete   -> ConfigError (a path the operator fixes)
  * model load or inference blew up       -> ModelInferenceError (retryable)

No engine stub: if there is no engine and no weights, the solver fails loudly
instead of returning an empty string that looks like a low-confidence answer.
"""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from captcha.domain.errors import ConfigError, ModelInferenceError

if TYPE_CHECKING:
    from PIL.Image import Image

_PADDLE_LOCK = threading.Lock()
_TROCR_LOCK = threading.Lock()
_paddle_instance: Any = None
_paddle_key: tuple[str, tuple[str, ...]] | None = None
_trocr_instance: tuple[Any, Any, str, str] | None = None  # (processor, model, device, source)

_DEFAULT_TROCR = "microsoft/trocr-base-printed"

_PADDLE_HINT = (
    "PaddleOCR is not installed in this worker image (needs paddlepaddle + paddleocr). "
    "Run the ocr lane on an image that has them; the base image deliberately does not."
)
_TROCR_HINT = (
    "TrOCR runtime is not installed in this worker image (needs torch + transformers). "
    "Run the ocr lane on an image that has them; the base image deliberately does not."
)


def have_paddle() -> bool:
    """True when the paddleocr package can be imported (no heavy import here)."""
    return importlib.util.find_spec("paddleocr") is not None


def have_trocr() -> bool:
    """True when both torch and transformers are importable."""
    return importlib.util.find_spec("torch") is not None and importlib.util.find_spec("transformers") is not None


def available_engines() -> list[str]:
    found = []
    if have_paddle():
        found.append("paddle")
    if have_trocr():
        found.append("trocr")
    return found


def _checked_dir(value: str | Path, field: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_dir():
        raise ConfigError(f"ocr: {field} does not exist: {path}", context={"field": field, "path": str(path)})
    return path


def _paddle_dirs(dirs: dict[str, str] | None) -> dict[str, str]:
    if not dirs:
        return {}
    out: dict[str, str] = {}
    for key in ("det_model_dir", "rec_model_dir", "cls_model_dir"):
        value = dirs.get(key)
        if value:
            out[key] = str(_checked_dir(value, key))
    return out


def get_paddle(lang: str = "en", *, dirs: dict[str, str] | None = None) -> Any:
    """Cached PaddleOCR instance. Raises ConfigError/ModelInferenceError, never a stub."""
    global _paddle_instance, _paddle_key
    model_dirs = _paddle_dirs(dirs)
    key = (lang, tuple(sorted(model_dirs.items())))
    with _PADDLE_LOCK:
        if _paddle_instance is not None and _paddle_key == key:
            return _paddle_instance
        try:
            from paddleocr import PaddleOCR  # type: ignore[import-not-found]
        except Exception as exc:
            raise ConfigError(_PADDLE_HINT, context={"import_error": repr(exc)}) from exc

        errors: list[str] = []
        for extra in ({"use_angle_cls": True, "show_log": False}, {"use_angle_cls": True}, {}):
            try:
                ocr = PaddleOCR(lang=lang, **model_dirs, **extra)
            except TypeError as exc:  # older/newer PaddleOCR kwarg drift
                errors.append(repr(exc))
                continue
            except Exception as exc:
                raise ModelInferenceError(
                    f"PaddleOCR init failed: {exc}",
                    context={"lang": lang, "dirs": model_dirs},
                ) from exc
            _paddle_instance, _paddle_key = ocr, key
            return ocr
        raise ModelInferenceError(
            "PaddleOCR rejected every known init signature",
            context={"lang": lang, "errors": errors},
        )


def get_trocr(device: str = "cpu", *, model_path: str | None = None) -> tuple[Any, Any, str]:
    """Cached (processor, model, device). `model_path` is a local HF directory."""
    global _trocr_instance
    source = str(_checked_dir(model_path, "model_path")) if model_path else _DEFAULT_TROCR
    with _TROCR_LOCK:
        if _trocr_instance is not None and _trocr_instance[3] == source and _trocr_instance[2] == device:
            return _trocr_instance[0], _trocr_instance[1], _trocr_instance[2]
        if model_path:
            config = Path(source) / "config.json"
            if not config.is_file():
                raise ConfigError(
                    "ocr: local TrOCR weights are incomplete (config.json missing)",
                    context={"model_path": source},
                )
        try:
            import torch  # type: ignore[import-not-found]
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel  # type: ignore[import-not-found]
        except Exception as exc:
            raise ConfigError(_TROCR_HINT, context={"import_error": repr(exc)}) from exc
        try:
            processor = TrOCRProcessor.from_pretrained(source)
            model = VisionEncoderDecoderModel.from_pretrained(source)
            model = model.to(device)
            model.eval()
            torch.set_grad_enabled(False)
        except Exception as exc:
            raise ModelInferenceError(
                f"failed to load TrOCR model '{source}': {exc}",
                context={"model": source, "device": device},
            ) from exc
        _trocr_instance = (processor, model, device, source)
        return processor, model, device


def _paddle_result_text(node: Any, out: list[str]) -> None:
    rec_texts = getattr(node, "rec_texts", None)
    if rec_texts is None and isinstance(node, dict):
        rec_texts = node.get("rec_texts")
    if rec_texts is not None and len(rec_texts):
        out.extend(str(t) for t in rec_texts if t)
        return
    if isinstance(node, (list, tuple)):
        # classic shape: [box, (text, confidence)]
        if len(node) >= 2 and isinstance(node[1], (list, tuple)) and node[1] and isinstance(node[1][0], str):
            out.append(node[1][0])
            return
        for item in node:
            _paddle_result_text(item, out)


def paddle_read(image: Image, lang: str = "en", *, dirs: dict[str, str] | None = None) -> str:
    """Run PaddleOCR on a PIL image and return concatenated text."""
    import numpy as np

    ocr = get_paddle(lang, dirs=dirs)
    arr = np.array(image.convert("RGB"))
    try:
        fn = getattr(ocr, "ocr", None)
        if fn is not None:
            try:
                result = fn(arr, cls=True)
            except TypeError:
                result = fn(arr)
        else:
            result = ocr.predict(arr)
    except Exception as exc:
        raise ModelInferenceError(f"PaddleOCR failed: {exc}", context={"lang": lang}) from exc

    pieces: list[str] = []
    _paddle_result_text(result, pieces)
    return " ".join(piece.strip() for piece in pieces if piece.strip()).strip()


def trocr_read(image: Image, device: str = "cpu", *, model_path: str | None = None) -> str:
    """Run TrOCR on a PIL image and return decoded text."""
    import torch  # type: ignore[import-not-found]

    processor, model, device = get_trocr(device, model_path=model_path)
    try:
        pixel_values = processor(images=image.convert("RGB"), return_tensors="pt").pixel_values.to(device)
        with torch.inference_mode():
            generated_ids = model.generate(pixel_values)
        text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
    except Exception as exc:
        raise ModelInferenceError(f"TrOCR inference failed: {exc}", context={"device": device}) from exc
    return str(text).strip()
