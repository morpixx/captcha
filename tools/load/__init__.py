"""Load-test tooling: 2captcha createTask/getTaskResult driver + local stack.

Operator tooling, not part of the `captcha` distribution: nothing under
`tools/` is imported by `src/captcha`. Entry points:

    python -m tools.load.run        # the load driver (HTTP client, any endpoint)
    python -m tools.load.devstack   # redis+postgres+g in-process gateway and fake workers

How to read the numbers — `docs/load-testing.md`.
"""

from __future__ import annotations
