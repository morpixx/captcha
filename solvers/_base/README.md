# captcha-solver-base

Solver ABC, entry-points registry, test harness.

Every type plugin does:

```python
from captcha_solver_base import Solver, SolverContext, SolverInfo, register_solver
from captcha_common import CaptchaType, Task, SolverResult

@register_solver
class FriendlySolver(Solver):
    info = SolverInfo(name="friendly", types=(CaptchaType.FRIENDLY,), http=True)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        ...
```

And exports its class via `pyproject.toml`:

```toml
[project.entry-points."captcha.solvers"]
friendly = "captcha_solver_pow.friendly:FriendlySolver"
```

The worker calls `discover_solvers()` at startup; nothing to wire by hand.
