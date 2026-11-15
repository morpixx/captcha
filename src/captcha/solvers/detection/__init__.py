from captcha.solvers.detection.bbox import BBoxSolver
from captcha.solvers.detection.click import ClickSolver
from captcha.solvers.detection.detector import (
    Detection,
    Detector,
    DetectorSpec,
    resolve_class_ids,
)
from captcha.solvers.detection.detector_loader import load_detector
from captcha.solvers.detection.drag import DragDropSolver
from captcha.solvers.detection.draw import DrawAroundSolver
from captcha.solvers.detection.onnx_detector import OnnxDetector

__all__ = [
    "BBoxSolver",
    "ClickSolver",
    "Detection",
    "Detector",
    "DetectorSpec",
    "DragDropSolver",
    "DrawAroundSolver",
    "OnnxDetector",
    "load_detector",
    "resolve_class_ids",
]
