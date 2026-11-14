"""captcha — HTTP-first captcha solver (2captcha/capsolver compatible).

Layers, top to bottom: services (composition) -> application (use cases)
-> domain (pure). Adapters implement domain ports; solvers and engines are
plugins that only know domain + leaf libraries.
"""

__version__ = "0.1.0"
