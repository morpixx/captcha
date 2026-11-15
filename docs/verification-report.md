# Verification report — 2026-11-15

**Worktree:** `D:/wt/captcha-verifier` · **branch:** `feat/verifier` · **HEAD:** `f20b234` (merge: Akamai and Kasada V8 harness flows)
**Verifier:** read-only; this file is the only change. No live captcha/API calls, no push/merge, no edits to `.env`/`data`/keys.

This report records facts produced by commands in this worktree. Every number below is reproducible with the commands in §8.

## 0. Gate summary

| # | gate | status | evidence |
|---|---|---|---|
| 1 | `uv run pytest -q` | **PASS** | 1503 passed, 2 skipped, 32 warnings in 29.21s |
| 2 | `uv run ruff check src tests` | **PASS** | `All checks passed!` |
| 3 | `uv run lint-imports` | **PASS** | 439 files / 2251 deps; 6 contracts kept, 0 broken |
| 4 | `docker compose -f docker-compose.yml config --quiet` | **PASS** | exit 0, no output |
| 5 | solver inventory, boot without `cv` | **PASS** | 60/60 modules loaded, 0 skipped, 63/63 types covered |
| 6 | `docs`/`runbooks` relative links | **PASS** | 32 files before this report, 33 with it; 64 relative links, 0 broken |
| 7 | secrets / keys / proxy grep | **PASS** | 0 live secrets; 27 regex candidates all triaged as templates/defaults |
| 8 | production-contract items (§1–§10) | **FAIL** | see §4: 8 pass, 6 fail |
| 9 | final gate 62/62 L3 | **FAIL** | 0/62 types have recorded L3 evidence; no canary fixtures exist |

Gate 1 caveat: in a clean worktree `uv run pytest -q` alone is **not** hermetic — the worktree had no `.venv`/`uv.lock`, and `uv run` fell through to the system `pytest.exe` with the project `.venv` missing `msgspec`, producing 74 collection errors. After `uv sync --extra dev --offline` (same install step as CI) the command is green. Reproduce with the sync first, or use `uv run --extra dev pytest -q`.

## 1. Commands and raw results

### 1.1 Tests

```
$ uv sync --extra dev --offline          # worktree had no .venv/uv.lock; uv cache only
$ uv run pytest -q
........................................................................ [100%]
1503 passed, 2 skipped, 32 warnings in 29.21s
```

Skipped, both environment-gated, not failures:

- `tests/solvers/classification/test_backbone.py:65` — `torch+hf extras not installed`
- `tests/solvers/hcaptcha/test_http.py:524` — `needs node plus jsdom`

Targeted contract subset (`tests/services/test_escalation.py tests/adapters/test_capabilities.py tests/adapters/test_netguard.py tests/gateway/test_security_hardening.py tests/services/test_canary.py`): `90 passed`.

### 1.2 Lint and architecture

```
$ uv run ruff check src tests
All checks passed!

$ uv run lint-imports
Analyzed 439 files, 2251 dependencies.
domain stays pure: no I/O, no upper layers KEPT
application depends on domain only KEPT
adapters never reach up KEPT
engines never reach up KEPT
solvers never touch adapters KEPT (2 ignored imports)
leaf libraries stay leaves KEPT
Contracts: 6 kept, 0 broken.
```

### 1.3 Compose

```
$ docker compose -f docker-compose.yml config --quiet
(no output, exit 0)
```

Compose includes `worker-browser` and `canary` behind profiles (lines 183, 231). The canary service mounts `./tests/canary/fixtures` (line 242), which does not exist — `config` does not validate bind-mount sources, so this gate cannot catch it.

### 1.4 Inventory (no `cv`)

Run with `PYTHONPATH=D:/wt/captcha-verifier/src` and `D:/captcha/.venv/Scripts/python.exe`; `cv2=False`, `torch=False` in both that venv and the `uv` venv.

```
captcha_file: D:\wt\captcha-verifier\src\captcha\__init__.py
SOLVERS_table: 60
registered_solvers: 60
registered_types: 63
experimental_solvers: 42
lane_counts: {'http': 20, 'cv': 39, 'protocol': 2, 'browser': 2}
NO_SOLVER: []
NO_HTTP_NO_BROWSER: []
NO_BROWSER_COUNT: 52
NO_HTTP: []
```

`cv: 39` includes the custom `hopscotch` type; for the 62 vendor types the split is `http 20 / cv 38 / protocol 2 / browser 2`.

### 1.5 Docs links

Script scans `docs/**/*.md`, `runbooks/**/*.md`, `README.md`, `SECURITY.md`, `AGENTS.md`, ignores fenced/inline code, resolves targets relative to the file, ignores `http(s)`/`#`/`mailto`.

```
files: 32 relative_links: 64 broken: 0
```

Re-run with this report present: `files: 33 relative_links: 64 broken: 0`. One earlier hit, `docs/reverse-2026.md:14`, was a false positive from inline code `p[$_CEEJe(1196)](o)`; it disappears once code spans are masked and is not a link.

### 1.6 Secrets / keys / proxies

Patterns scanned over `src/`, `docs/`, `runbooks/`: PEM private key, AWS AKID, GitHub/Slack/OpenAI/Google tokens, JWT, `scheme://user:pass@host`, assignments to password/secret/api_key/clientKey/admin_token. 27 candidates were found; each one was masked and triaged:

```
CATEGORIES: {'proxy_creds': 14, 'assigned_secret': 13}
TOTAL: 27
```

All 27 are docstrings, f-strings, type annotations, or placeholders: `config.py:24` / `type_map.py:10,252` / `proxy.py` / `transport/curl.py` describe URL shape; `providers.py` builds `f"{scheme}://{login}:{password}@{host}"` from runtime config; `tasks.py:248` reads `body.client_key`; `models.py:37` is a type annotation. No PEM block, no cloud/GitHub token, no JWT, no literal proxy credentials.

Non-secret defaults exist and are expected dev values, not live credentials: `POSTGRES_PASSWORD: captcha` (`docker-compose.yml:64`), `MINIO_ROOT_PASSWORD: minioadmin` (line 80), `sqlalchemy.url = postgresql+asyncpg://captcha:captcha@localhost:5432/captcha` (`src/captcha/adapters/storage/alembic.ini:8`). `.env.example` contains only placeholders; no `.env` exists in this worktree.

A regex scan cannot prove absence of secrets; it proves the listed patterns do not match. No values were printed.

## 2. Inventory details

| metric | value |
|---|---|
| `CaptchaType` values | 63 = 62 vendor + custom `hopscotch` |
| `SOLVERS` entries in `solvers/loader.py` | 60 |
| solvers registered at runtime | 60 (0 skipped) |
| types with at least one solver | 63/63 |
| `experimental=True` solvers | 42/60 (41 vendor + `hopscotch`) |
| `experimental=True` vendor types | 38/62 |
| non-experimental vendor types | 24/62 |
| lane split, vendor types | `http 20`, `cv 38`, `protocol 2`, `browser 2` |
| lane of `hopscotch` | `cv` |
| `# FILL:` markers in `src/captcha` | 101 |

### 2.1 Path coverage per type

Declared ABI path (any solver with `info.http` / `info.browser`; `cv` is the catalog lane):

| path | types without it | count |
|---|---|---|
| `http` | none — every type has at least one `http=True` solver | **0** |
| `browser` | complement of the 11 browser-capable types below | **51** of 62 |
| `cv` lane | all `http` (20) + `protocol` (2) + `browser` (2) types | **24** of 62 |

Browser-capable types (11): `hcaptcha`, `hcaptcha_enterprise`, `datadome_slider`, `datadome`, `funcaptcha`, `funcaptcha_enterprise`, `turnstile`, `turnstile_invisible`, `cf_challenge`, `alibaba`, `hunt`.

No-cv-lane types (24): `friendly_captcha`, `altcha`, `hcaptcha`, `hcaptcha_enterprise`, `recaptcha_v3`, `recaptcha_v3_enterprise`, `datadome_slider`, `datadome`, `funcaptcha_bda`, `turnstile`, `turnstile_invisible`, `cf_challenge`, `akamai_sensor`, `akamai_sbsd`, `kasada`, `perimeterx`, `imperva`, `castle`, `tspd`, `cybersiara`, `twitch_integrity`, `botguard`, `botdeflector`, `temu`.

The `http=True` flag does not mean "solves without models": slider/rotate/classification/detection solvers set it while their CV imports stay lazy and fail at solve time with `ConfigError` when `cv2`/`torch` are absent.

### 2.2 Registered but non-functional

Eight solvers end in `not_implemented()` and cannot pass any live gate: `castle`, `tspd`, `botguard`, `perimeterx`, `botdeflector`, `cybersiara`, `imperva`, `twitch_integrity` (`solvers/antibot/*/solver.py`). Additional partial paths marked in code: Akamai `sec-cpt` / HTTP 428 (`antibot/akamai/solver.py:173`), AtbCAPTCHA plaintext instead of AES (`interactive/atbcaptcha/solver.py:116`), Yandex `token_stub` (`interactive/yandex/solver.py:55`).

## 3. What loads without `cv`

`discover_solvers()` returns 60/60 entries and covers 63/63 types with no `cv2`/`torch` in the interpreter. This **contradicts** the docs: `docs/architecture.md:303-310` says 58 loaded and `slider`/`rotate` skipped, `AGENTS.md:99-100` says 58/56, `docs/plan.md:120` says 58, `README.md:47-48` says 58. Actual boot is clean; the solvers import cv2/torch lazily inside their CV paths.

## 4. Production-contract matrix

| contract item | status | evidence | gap |
|---|---|---|---|
| §3 `SolverInfo` / `Solver` ABI | **PASS** | `solvers/base.py:13-23` fields and defaults; `solve_http` abstract, `solve_browser` default raises, `supports` filters modes (`:70-86`) | — |
| §2 `NeedsBrowserError` + one hop | **PASS** | `domain/task.py:13` `MAX_LANE_HOPS=1`; `worker_http/worker.py:225,297`; second hop inside browser lane becomes `ERR_UNSOLVABLE` (`worker_browser/runner.py:93-106`) | — |
| §2 capability keys | **PASS** | `adapters/queue/capabilities.py:30-135` — `SETEX`, `SADD`, TTL 90, stale lane ignored | only browser worker writes them (next row) |
| §2/§7 gateway heartbeat gating | **FAIL** | only `worker_browser` starts `LaneHeartbeat`; `worker_http`/`worker_cv`/`worker_protocol` never heartbeat. Gateway snapshots live types once at boot (`gateway/app.py:54-61`) and gates only the browser lane (`:79-90`) | a gateway with only http/cv/protocol workers answers `/readyz` 503; a browser worker that dies after boot stays sellable until gateway restart; `CAPTCHA_EXTRA_TYPES` does not exist |
| §8 `netguard` on client URLs | **FAIL** | module is implemented and unit-tested (`adapters/netguard.py`, `tests/adapters/test_netguard.py`), but the only production call sites are browser navigation `adapters/browser/provider.py:198,213` | `pageurl`, `websiteURL`, `challenge_url`, `surl`, `api_server` are not validated in the gateway or HTTP solvers; `SECURITY.md:78,144` states this openly |
| §8 redaction | **PASS** | `domain/redaction.py`; Postgres copy `routes/tasks.py:109`, `routes/legacy.py:179`; DLQ/retry copies `adapters/queue/streams.py:62,119,287` | — |
| §8 quotas | **PASS** | `services/gateway/quotas.py` (4 limits, Redis); called before charge in `routes/tasks.py:83-92` | `QuotaError`/`ERR_QUOTA` is defined but never raised; outward code is 2captcha `ERR_NO_SLOT` per `docs/protocol.md:180-181` |
| §8 API-key hash at rest | **PASS** | `domain/apikey.py` (HMAC-SHA256 + pepper, `key_tail`); `adapters/storage/repos.py`; migration `0004_security.py` | — |
| §10 canary fixtures | **FAIL** | `CANARY_FIXTURES_DIR` defaults to `tests/canary/fixtures` (`services/canary/cli.py:46`) but the directory does not exist and was never tracked (`git ls-files tests/canary` is empty) | suite logs `canary.run.no_fixtures` and reports `pass_rate=1.0`; zero per-type measurements are possible |
| §7 `ALLOW_EXPERIMENTAL_TASKS` | **FAIL** | name appears only in `docs/*.md`; no field in `config.py`; `worker_http/worker.py:126` hardcodes `allow_experimental=True` | 38 experimental types are sold today; contract requires default `0` |
| §6 official type names / proxy / payload | **PASS** | `services/gateway/type_map.py` maps official names and composes flat proxy fields (`:244-343`); legacy payload redacted before persistence | — |
| §4 browser provider | **PASS (code/tests)** | `adapters/browser/{provider,prefs,supervisor,geoip}.py`; fresh profile, GeoIP via exit IP, process-tree kill; covered by tests | live Camoufox run is not recorded in this worktree; `camoufox` is not installed |
| §1 runbook per type | **FAIL** | 7 type runbook files exist and declare 10 types (`capy`, `funcaptcha` + `_enterprise` + `_bda`, `geetest_v3`, `geetest_v4`, `hcaptcha` + `_enterprise`, `keycaptcha`, `lemin`) | 52 of 62 types have no dedicated runbook; `runbooks/README.md:138` still says 7 covered / 55 pending |
| §1/§2 L3 for 62 types | **FAIL** | no canary fixtures, no 7-day run, no `captcha_type_health` history | **0/62** types evidence L3; plan §2.5 expected 0, this run confirms no evidence exists |

Also factual: `docs/protocol.md` says `ERR_QUOTA` is not thrown by anyone, and that matches the code.

## 5. Blockers to 62/62

Ordered by what stops the gate first.

1. **B1 — canary has no fixtures.** `tests/canary/fixtures` is absent, so the canary process measures nothing and reports success on an empty set. Without fixtures there is no L1/L2/L3 evidence for any type. (`services/canary/cli.py:46`, `services/canary/suite.py:176`, compose line 242.)
2. **B2 — `ALLOW_EXPERIMENTAL_TASKS` is not implemented.** 38/62 vendor types are `experimental=True` and still sold because the HTTP worker hardcodes `allow_experimental=True`; the gateway does not filter either. Contract §7 is not enforceable today.
3. **B3 — heartbeat/capabilities are browser-only and boot-time.** Only the browser worker beats; the gateway snapshots `live_types` once and gates only the browser lane. A default stack (http/cv/protocol, no browser profile) fails `/readyz`, and a dead browser worker remains sellable until gateway restart. Contract §2/§7 requires fresh heartbeat per lane and `registry ∪ CAPTCHA_EXTRA_TYPES ∪ heartbeat caps`.
4. **B4 — netguard is not wired to client-supplied URLs.** The SSRF guard exists and passes its unit tests but is called only for browser navigation. `pageurl`/`websiteURL`/`challenge_url`/`surl`/`api_server` from createTask reach HTTP solvers unvalidated.
5. **B5 — eight types are hard stubs.** `castle`, `tspd`, `botguard`, `perimeterx`, `botdeflector`, `cybersiara`, `imperva`, `twitch_integrity` call `not_implemented()`; they can never reach L3 until the wire is implemented.
6. **B6 — 52/62 types have no dedicated runbook.** Plan §1 "type closed" requires a runbook (or explicit family coverage); the index still claims 7/55.
7. **B7 — 0/62 evidenced at L3.** No type has a 7-day canary, pass/p95/coverage numbers, or 7 days of `captcha_type_health=1`; by the contract's own rule, none is sellable.
8. **B8 — gate docs are stale.** Several numbers in `AGENTS.md`, `README.md`, `docs/architecture.md`, `docs/plan.md` no longer describe the tree (details in §6). A gate that cites stale evidence cannot be trusted.

## 6. Documentation drift found during verification

| file:line | claim | fact |
|---|---|---|
| `README.md:28` | worker-browser "в compose не описан" | `docker-compose.yml:183` defines `worker-browser` behind the `browser` profile |
| `README.md:32` | "api-key hash ..., netguard, redaction \| работает" | redaction and key hashing work; netguard is not wired outside browser navigation |
| `README.md:47-48` | 58 load without cv, 60/62 types | 60/60 load, 63/63 types (62 + hopscotch) |
| `AGENTS.md:99-100` | 58 enrolled, 56 registered at runtime, 41/58 experimental | 60 enrolled, 60 registered, 42/60 experimental |
| `AGENTS.md:104-105` | three lanes only, "there is no browser lane" | `browser` lane exists: `domain/catalog.py` browser types, `worker_browser`, compose profile |
| `AGENTS.md:106` | 262 passing, 3 skipped | 1503 passed, 2 skipped |
| `docs/architecture.md:303-310` | 58 loaded; slider and rotate skipped | 60 loaded, 0 skipped; cv imports are lazy |
| `docs/architecture.md:321-322` | no reverse-proxy / TLS ingress in the repo | `infra/caddy/Caddyfile` + `caddy` compose service provide TLS ingress |
| `docs/architecture.md:323` | worker-browser and canary not in compose | both are in compose behind profiles |
| `docs/architecture.md:325` | six type runbooks (7 types) | 7 type runbook files declare 10 types |
| `docs/plan.md:120` | 58 load without cv (60/62) | 60/60, 63/63 |
| `docs/baseline.md:9,17,21` | 262/3, 40/56 experimental, no official type map | historical snapshot; current tree: 1503/2, 42/60, type map implemented |
| `runbooks/README.md:138` | 7 types covered / 55 pending | 7 files declare 10 types; 52 lack a dedicated runbook |
| `README.md:31` | canary "работает, фикстур ещё нет" | accurate, but "works" is only the empty-set path |

`SECURITY.md` is the one document that correctly states netguard is not connected, and it also documents the empty-canary trap.

## 7. Recommendations

1. **Canary first.** Commit a fixture corpus under `tests/canary/fixtures` (at least the plan §2 L1 counts per family), add a CI check that the directory is non-empty, and make the canary daemon exit non-zero on `no_fixtures` in production mode. Nothing else can produce L3 evidence until this exists.
2. **Implement the experimental gate.** Add `ALLOW_EXPERIMENTAL_TASKS: bool = False` to `Settings`; filter `state.solver_types` and `SolverRegistry.pick` by it; pass it from `worker_http` instead of hardcoding `True`. Test: experimental type gets `ERROR_TASK_NOT_SUPPORTED` with flag 0 and is sold with flag 1.
3. **Heartbeat every lane.** Start `LaneHeartbeat` from `worker_http`, `worker_cv`, `worker_protocol` with their solver type sets; add `CAPTCHA_EXTRA_TYPES`; refresh `gateway_live_types` per request (or on a short TTL) and gate each lane's types, not only browser. Add a test where no browser heartbeat exists but `/readyz` is still 200 because http is alive.
4. **Wire netguard at the gateway boundary.** Validate `pageurl`, `websiteURL`, `challenge_url`, `surl`, `api_server` with an injectable resolver before persistence/queueing, and validate redirect hops in the HTTP transport. Keep the existing unit tests and add a createTask test that a private URL returns `ERR_URL_REJECTED` without charging.
5. **Close or quarantine the eight stubs.** Implement the wire for the 8 antibot types, or explicitly mark them non-sellable and remove them from capability advertisement until they pass L3.
6. **Write the missing runbooks.** 52 types; family runbooks can cover a group only if they are listed in `runbooks/README.md` as explicit family coverage. Update the index count.
7. **Run the L3 clock per type.** After 1–6, run 7 days of canary per type, record pass/p95/coverage and `captcha_type_health`, then flip `experimental=False` one type at a time. The verifier signs off only on recorded artifacts.
8. **Fix the gate docs.** Update the numbers in `AGENTS.md`, `README.md`, `docs/architecture.md`, `docs/plan.md`, `docs/baseline.md` and `runbooks/README.md`; a docs-only PR owned by the docs role. Also correct `README.md:32` netguard wording to "module + tests, not wired" until recommendation 4 lands.
9. **Keep the checks.** Add `uv run pytest -q` (full suite), `docker compose config`, the relative-link checker and the secret scan to CI; today CI runs only `tests/domain tests/solvers/pow` (`.github/workflows/ci.yml`), so a green CI is narrower than this gate.

## 8. Reproduction

```powershell
# environment (worktree has no .venv/uv.lock)
uv sync --extra dev --offline
uv run pytest -q
uv run ruff check src tests
uv run lint-imports
docker compose -f docker-compose.yml config --quiet

# inventory, no cv
$env:PYTHONPATH='D:/wt/captcha-verifier/src'
D:/captcha/.venv/Scripts/python.exe -c "from captcha.solvers.registry import discover_solvers; r=discover_solvers(); print(len(list(r.all_solvers())), len(r.all_types()))"

# docs links / secrets: scripts used in this run were kept outside the repo
# (C:\Users\Roman\AppData\Local\Temp\verifier-links2.py, verifier-secrets.py)
```

`uv.lock` and `.venv/` produced by the sync are gitignored; they are not part of the report change.
