"""Hopscotch training kit (ml side of the custom hopscotch type).

Turns the read-only hopscotch_v2 dataset into a frozen split, drives that repo's
trainer and evaluator, exports a run into the serving artifact triple, and
publishes it. Nothing in this package is imported by the service; the runtime
contract lives in captcha.solvers.hopscotch.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
