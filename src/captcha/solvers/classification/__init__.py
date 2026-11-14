from captcha.solvers.classification.awswaf import AWSWAFSolver
from captcha.solvers.classification.base_grid import GridClassifier
from captcha.solvers.classification.funcaptcha_grid import FunCaptchaGridSolver
from captcha.solvers.classification.hcaptcha import HCaptchaSolver
from captcha.solvers.classification.recaptcha_grid import RecaptchaGridSolver
from captcha.solvers.classification.shein import SheinSolver

__all__ = [
    "AWSWAFSolver",
    "FunCaptchaGridSolver",
    "GridClassifier",
    "HCaptchaSolver",
    "RecaptchaGridSolver",
    "SheinSolver",
]
