from captcha_solver_classification.awswaf import AWSWAFSolver
from captcha_solver_classification.base_grid import GridClassifier
from captcha_solver_classification.funcaptcha_grid import FunCaptchaGridSolver
from captcha_solver_classification.hcaptcha import HCaptchaSolver
from captcha_solver_classification.recaptcha_grid import RecaptchaGridSolver
from captcha_solver_classification.shein import SheinSolver

__all__ = [
    "AWSWAFSolver",
    "FunCaptchaGridSolver",
    "GridClassifier",
    "HCaptchaSolver",
    "RecaptchaGridSolver",
    "SheinSolver",
]
