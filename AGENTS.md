# AGENTS.md

Operating manual for an AI coding agent working in this repository. Read this before
touching code. Humans: the same rules apply, see `docs/workflow.md` for the process.

## What this is

A captcha-solving service with a 2captcha-compatible HTTP API. One Python distribution
(`captcha`), layered, with plugins for 62 captcha types and 12 antibot engines. Most of
the code is adaptation to other people's protocols; the core is deliberately small.

Non-negotiables: the layer direction, the per-type solution shape, and the fact that a
worker must boot without optional extras (`cv`, `browser`) installed.

## Read first

| file | why |
|---|---|
| `docs/architecture.md` | layers, tree, dependency table, what is not done yet |
| `docs/protocol.md` | API contract, error codes, per-type solution keys |
| `docs/workflow.md` | git flow, branch rules, PR checklist |
| `runbooks/_template.md` | how a per-type runbook is written (required with a new solver) |

## Golden rules

1. **Dependencies point one way:** `services → application → domain`; `adapters → domain`;
   `engines`/`solvers → domain` (+ leaf libs). Never the reverse. `make imports` enforces it.
2. **`domain/` imports stdlib only.** No redis, sqlalchemy, fastapi, structlog-by-config, no I/O.
3. **No `get_settings()` outside `services/`.** Config is read once in a composition root and
   passed down. One documented exception exists in two text solvers; don't copy it.
4. **Run everything through `uv run`.** No venv activation, no bare `python`/pytest on PATH.
5. **A worker must start without `--extra cv`.** Anything importing torch/opencv/playwright goes
   inside a function or behind `_register_one`-style try/except.
6. **Errors are translated at the adapter boundary.** Solvers and domain raise only
   `captcha.domain.errors.CaptchaError` subclasses; the code becomes an `ERR_*` in the API.
7. **File size cap:** solver ≤ 200 lines, adapter ≤ 250, everything else ≤ 400. Over the cap
   means the module does two jobs — split it.
8. **One captcha type = one solver file.** If two types share >70% of code (GeeTest v3/v4,
   slider engines), one file parameterised by type is fine.
9. **Never invent API shapes.** `domain/solution.py` encodes what 2captcha clients read per
   type; if a type's shape is unknown, read `docs/protocol.md` and the reference clients before
   guessing.
10. **Docs move with code.** New type → runbook + `domain/enums.py`. API change →
    `docs/protocol.md`. Tree/layer change → `docs/architecture.md`. Same PR.

## Layout

```
src/captcha/
├── config.py            Settings (pydantic-settings); imported by services only
├── domain/              pure core: enums, task, solution, errors, pricing, lanes, profile
├── application/         use-case level policy (lane routing)
├── adapters/            transport(curl_cffi) queue(redis) storage(postgres) jssandbox
│                        weights(s3) proxy profiles browser
├── engines/             <engine>/builder.py — payload assembly per antibot engine;
│                        base.py, registry.py, __init__.py registers all builders
├── solvers/             base.py (Solver ABC), registry.py, loader.py (enrollment table),
│                        harness.py + one package per captcha family
├── crypto/ behavior/    leaf libraries, no imports outside domain
├── observability/       logging, metrics, tracing, sentry
└── services/            gateway, worker_http, worker_cv, worker_protocol, worker_browser,
                         canary, admin, pipeline — cli.py + __main__.py + wiring
tests/                   tests/domain, tests/solvers/<family>
docs/ runbooks/ infra/   documentation, per-type runbooks, docker
```

## Current coverage (keep it accurate when you change it)

- `CaptchaType`: 62 values. `AntibotEngine`: 12.
- Solvers enrolled in `solvers/loader.py`: 53. Registered at runtime without extras: 51
  (`slider` and `rotate` need opencv from `--extra cv`).
- Types with no solver yet: `geetest_v3`, `geetest_v4`, `capy`, `keycaptcha`, `lemin`
  (plus `rotate`/`slider` if the cv extra is missing).

## Commands

```bash
uv sync --extra dev            # base + tooling; add --extra cv for torch/opencv
make lint                      # ruff check src tests
make fmt                       # ruff format
make typecheck                 # mypy src
make test                      # pytest -q
make imports                   # import-linter: six architecture contracts
make dev                       # docker compose: gateway + workers + redis/pg/minio
make canary                    # regression run against a live gateway
```

CI runs `ruff`, `lint-imports`, and `pytest tests/domain tests/solvers/pow` on Python 3.14.
CV tests need torch and run in a separate job later.

## Recipes

### Add a captcha type's solver

1. `src/captcha/solvers/<family>/<type>.py`: subclass `Solver`, fill `info = SolverInfo(name=..., types=(CaptchaType.X,), http=True)`, implement `async solve_http(task, ctx)`.
2. Return `SolverResult` with the canonical field (`token`, `text`, `coords`, `offset`, `angle`,
   `bbox`, `indices`). Vendor-specific keys go into `extra` — `domain/solution.py` passes
   `extra` through untouched.
3. Append `"captcha.solvers.<family>.<module>:<Class>"` to `SOLVERS` in `solvers/loader.py`.
4. Add a test under `tests/solvers/<family>/`.
5. Write `runbooks/<type>.md` from `runbooks/_template.md`.
6. If the type is new to the enum, add it to `domain/enums.py` and `domain/pricing.py`, and
   decide its lane in `domain/lanes.py`.

### Add an antibot engine payload builder

1. `src/captcha/engines/<engine>/builder.py`: subclass `SensorBuilder`, set `engine =`,
   implement `async build(ctx: BuildContext)`. Keep schema constants in that file, at the top,
   named after what the engine calls them.
2. Import + export it in `engines/__init__.py` (registering happens there).
3. Live dumps go to `engines/<engine>/fixtures/`; replay test in `tests/engines/`.
4. Pure helpers (AES/XOR/HMAC/deflate) come from `captcha.crypto`; do not re-implement.

### Add an adapter

Implement the port in `adapters/<tech>/`, translate foreign exceptions into domain errors,
export the type from that package's `__init__.py`. Never import it from `domain`,
`application`, `engines` or `solvers`.

### Add a service

`services/<name>/{__main__.py, cli.py}` + wiring inside `cli.py`; register the console script
in `pyproject.toml`; add a compose service running the same image with
`command: ["python","-m","captcha.services.<name>"]`.

## Testing

- `tests/domain` — pure logic: solution projection, pricing, lanes. Fast, no I/O.
- `tests/solvers/<family>` — patch the solver's collaborators (`unittest.mock.patch.object`),
  assert on `SolverResult`.
- `tests/engines/<engine>` — replay: feed a recorded payload from `fixtures/`, compare bytes.
- Do not write tests that need network; use fixtures.
- Fixtures: small samples committed under `engines/<engine>/fixtures/`, bulk dumps ignored.

## Git

Branch `<type>/<topic>` (`feat/`, `fix/`, `refactor/`, `docs/`, `chore/`), commits in the
imperative with the same prefixes. `main` is protected: PR + 1 approval + green `checks`.
Squash merge. Never force-push `main`.

## Do not

- Add a dependency without a reason in the PR description; heavy ones go to an extra.
- Import `captcha.adapters` from a solver or an engine.
- Put business decisions in adapters (prices, state transitions, retry policy live in domain/application).
- Edit `.import_linter_cache/`, `models-cache/`, `data/`, or any `__pycache__`.
- Commit `.env`, keys, proxies with credentials, model weights.
- Reformat unrelated files in a functional PR.
- Silence a failing test by weakening it; fix the code or delete the test with an argument.

## Definition of done

- [ ] `make lint imports test` green locally;
- [ ] new behaviour covered by at least one test;
- [ ] no layer violation (the contracts say so, not "I think so");
- [ ] worker still boots without optional extras if the change touched cv/browser code;
- [ ] docs/runbook updated where the change is user-visible;
- [ ] PR description says *why*, not just *what*.
