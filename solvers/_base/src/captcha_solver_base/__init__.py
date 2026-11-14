from captcha_solver_base.harness import SolverFixture, run_fixtures
from captcha_solver_base.registry import SolverRegistry, discover_solvers, register_solver
from captcha_solver_base.solver import Solver, SolverContext, SolverInfo

__all__ = [
    "Solver",
    "SolverContext",
    "SolverFixture",
    "SolverInfo",
    "SolverRegistry",
    "discover_solvers",
    "register_solver",
    "run_fixtures",
]
