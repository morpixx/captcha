# Verification report v2 — 2026-11-15

**Worktree:** `D:/wt/captcha-verifier2` · **branch:** `feat/verifier2` · **HEAD:** `0f54c14`
(merge: sync docs with 63 types, heartbeat, CI)
**Verifier:** read-only; this file is the only change. No live captcha/API calls, no network,
no push/merge, no edits to `.env`/`data`/keys.

This report re-checks the tree after the work merged between the previous report and this HEAD.
Every number below is reproducible with the commands in §8. Where a claim could not be checked
without a live account/proxy it is listed in §7, not asserted.

## Post-report fixes (2026-11-15, `edc3114`)

The body below is the `0f54c14` snapshot and is left as recorded. After it, `edc3114` —
*fix(capabilities): browser-gated types require a browser heartbeat* — closed B3, and the
doc pass closed B8:

| item | status now | evidence |
|---|---|---|
| B3 browser gate bypass | **CLOSED** | `CapabilityRegistry.live_types()` admits a type only from its catalog lane and admits `requires_browser()` types only from a live `browser` heartbeat; `worker_http._http_capabilities()` advertises `lane_for(ct) == "http" and not requires_browser(ct)`. Regression: `tests/adapters/test_capabilities.py::test_http_heartbeat_cannot_advertise_browser_types` (an HTTP heartbeat cannot sell `hcaptcha`/`turnstile`, still sells `recaptcha_v3`). |
| B1 canary fixtures | **CLOSED (code/corpus)** | 10 tracked fixtures / 10 types under `tests/canary/fixtures/`, pinned by `tests/canary/test_fixtures.py`. One replay case per type, not L1. |
| B5 hard stubs | **CLOSED (code)** | zero `not_implemented()` call sites under `src/captcha`; the helper at `solvers/antibot/_base.py:161` is dead code. |
| B8 stale gate docs | **CLOSED (docs)** | README, AGENTS, architecture, plan, operations, SECURITY, runbooks index and `.env.example` synced to the current tree. |

Inventory at `edc3114`: 63 `CaptchaType` (62 vendor + `hopscotch`), 60 `SOLVERS` / 60
registered, lanes 25/28/2/8 including `hopscotch` (vendor 25/27/2/8), 42 experimental
solvers = 38/62 experimental-only vendor types, 10 canary fixtures, 0 stubs, netguard wired
on gateway + transport + redirects, browser gate via heartbeat, 4 CI jobs. Gate 1 at this
commit: **2448 passed, 12 skipped** — the §0 run plus the one B3 regression test.

Still open: B2 is documented, not armed — `ALLOW_EXPERIMENTAL_TASKS` still defaults to
`True` (beta `1` in `.env.example`, prod `0`); B6/B7 unchanged; the final gate remains
**0/62 at L3**.

## 0. Gate summary

| # | gate | status | evidence |
|---|---|---|---|
| 1 | `uv run pytest -q` | **PASS** | 2447 passed, 12 skipped, 102 warnings in 25.70s |
| 2 | `uv run ruff check src tests` | **PASS** | `All checks passed!` |
| 3 | `uv run lint-imports` | **PASS** | 480 files / 2636 deps; 6 contracts kept, 0 broken |
| 4 | `docker compose -f docker-compose.yml config --quiet` | **PASS** | exit 0, no output |
| 5 | inventory, boot without `cv` | **PASS** | 60/60 modules loaded, 63/63 types, lanes 25/28/2/8 |
| 6 | canary fixtures | **PASS** | 10 files / 10 types tracked; pinned by `tests/canary/test_fixtures.py` |
| 7 | previous blockers (§3) | **PARTIAL** | 3 closed, 2 partially closed, 2 open, docs still stale |
| 8 | final gate 62/62 L3 | **FAIL** | 0/62 types have recorded L3 evidence |

**Environment note for gate 1.** The worktree has no `.venv` and no `uv.lock`, and the box is
offline. Plain `uv run` resolves the project then fails downloading `botocore==1.44.3`, which is
not in the uv cache:

```
$ uv sync --extra dev --offline
error: Failed to download `botocore==1.44.3`
  cause: Network connectivity is disabled, but the requested data wasn't found in the cache
```

The gate was run with the already-installed project venv, which is what `uv run` would use after
a successful sync:

```powershell
$env:UV_PROJECT_ENVIRONMENT='D:/captcha/.venv'
$env:PYTHONPATH='D:/wt/captcha-verifier2/src'
uv run --no-sync pytest -q
```

Cross-check with the plain interpreter (`D:/captcha/.venv/Scripts/python.exe -m pytest -q`,
`PYTHONPATH=.../src`) returned the same 2447 passed / 12 skipped. `.venv/` produced by the failed
sync is gitignored; `git status` stayed clean.

## 1. Commands and raw results

### 1.1 Full suite

```
$ uv run --no-sync pytest -q
2447 passed, 12 skipped, 102 warnings in 25.70s
```

Skipped (all environment-gated, none a failure):

- `tests/integration/browser/test_camoufox_local.py` — camoufox is not installed (1)
- `tests/integration/test_gateway_lifecycle.py` — Redis/Postgres test services unavailable (9)
- `tests/solvers/classification/test_backbone.py:65` — torch+hf extras not installed (1)
- `tests/solvers/hcaptcha/test_http.py:524` — node plus jsdom (1)

### 1.2 Lint and architecture

```
$ uv run ruff check src tests
All checks passed!

$ uv run lint-imports
Analyzed 480 files, 2636 dependencies.
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

The canary service mounts `./tests/canary/fixtures` (`docker-compose.yml:242`); the directory
now exists and is tracked.

### 1.4 Targeted gate tests

The files that pin the items under review — capabilities/heartbeat, experimental gate, netguard
at the gateway and in transport, redirects, canary fixtures — were run as one subset:

```
171 passed, 67 warnings in 1.94s
```

Files: `tests/adapters/test_capabilities.py`, `test_netguard.py`, `test_netguard_shape.py`,
`test_transport_netguard.py`, `test_transport_redirects.py`,
`tests/gateway/test_capabilities_wiring.py`, `test_netguard_urls.py`,
`tests/services/test_experimental_gate.py`, `test_heartbeat_lanes.py`,
`tests/canary/test_fixtures.py`.

Antibot solver suite (all engines, including the eight former stubs):

```
$ pytest -q tests/solvers/antibot
311 passed in 4.05s
```

## 2. Inventory

### 2.1 Counts

Command: `PYTHONPATH=src python -c ...` using `discover_solvers()` and `catalog.lane_for`.

| metric | value |
|---|---|
| `CaptchaType` values | 63 = 62 vendor + custom `hopscotch` |
| `SOLVERS` entries in `solvers/loader.py` | 60 |
| solvers registered at runtime | 60 (0 skipped) |
| types with at least one solver | 63/63 |
| `experimental=True` solvers | 42/60 |
| vendor types with no non-experimental solver (experimental-only) | 38/62 |
| vendor types with no experimental solver | 19/62 |
| vendor types with both (mixed) | 5/62: `classification_grid`, `datadome_slider`, `capy`, `keycaptcha`, `lemin` |
| vendor types sellable with `ALLOW_EXPERIMENTAL_TASKS=0` | 24/62 |
| `# FILL:` markers in `src/captcha` | 56 (previous report: 101) |
| `not_implemented()` call sites | 0 (helper remains at `solvers/antibot/_base.py:161`) |
| canary fixtures | 10 files, 10 types |
| runbooks indexed and non-pending | 37 = 36 vendor + `hopscotch`; 26 pending |

Precise experimental mapping: the 42 experimental solvers cover 44 type values (43 vendor +
`hopscotch`). "38 of 62" is correct for **experimental-only** vendor types, not for "types with
any experimental solver" (43).

### 2.2 Lanes

| lane | types incl. `hopscotch` | vendor only | membership |
|---|---|---|---|
| `http` | 25 | 25 | akamai_sbsd, akamai_sensor, alibaba, botdeflector, botguard, castle, cf_challenge, cybersiara, datadome, datadome_slider, funcaptcha, funcaptcha_bda, funcaptcha_enterprise, hunt, imperva, kasada, perimeterx, recaptcha_v3, recaptcha_v3_enterprise, temu, tspd, turnstile, turnstile_invisible, twitch_integrity, vk |
| `cv` | 28 | 27 | audio_captcha, awswaf, basilisk, binance, bounding_box, capy, classification_grid, click, coordinates, cutcaptcha, drag_drop, draw_around, geetest_v3, geetest_v4, hopscotch, image_to_text, keycaptcha, lemin, math_image, mtcaptcha, recaptcha_v2, recaptcha_v2_callback, recaptcha_v2_enterprise, recaptcha_v2_invisible, rotate, shein, slider, text_captcha |
| `protocol` | 2 | 2 | altcha, friendly_captcha |
| `browser` | 8 | 8 | atbcaptcha, captchafox, hcaptcha, hcaptcha_enterprise, prosopo, tencent, yandex, yidun |

Total 25/28/2/8 for all 63 types, which is the expected split. The vendor-only split is
25/27/2/8. `requires_browser()` additionally marks `cf_challenge`, `turnstile`,
`turnstile_invisible` — they sit in `http` but must escalate.

Path coverage in the solver ABI: every type has at least one `http=True` solver
(`NO_HTTP = []`); every type has at least one http-or-browser path
(`NO_HTTP_NO_BROWSER = []`). "http=True" still does not mean "solves without models": CV
solvers answer `ERR_CONFIG` when weights/OpenCV/torch are absent.

### 2.3 Canary fixtures

`tests/canary/fixtures/` is tracked and holds 10 cases:

| fixture | type | lane |
|---|---|---|
| `altcha_v1` | `altcha` | protocol |
| `datadome_tags` | `datadome` | http |
| `friendly_captcha_v1` | `friendly_captcha` | protocol |
| `funcaptcha_gt2` | `funcaptcha` | http |
| `geetest_v3_slide` | `geetest_v3` | cv |
| `geetest_v4_slide` | `geetest_v4` | cv |
| `hcaptcha_http_example_com` | `hcaptcha` | browser |
| `recaptcha_v2_audio` | `recaptcha_v2` | cv |
| `recaptcha_v3_score` | `recaptcha_v3` | http |
| `turnstile_widget` | `turnstile` | http |

Coverage is 10/62 vendor types, one fixture per type. Every note points to a recorded test
artefact ("replay constants", synthetic PoW, control key); none is a multi-day live
measurement, and none reaches the plan §2 L1 counts (>=100 labelled cases per type, >=300 for
grid families).

### 2.4 Former stubs

The eight solvers that previously called `not_implemented()` — `castle`, `tspd`, `botguard`,
`perimeterx`, `botdeflector`, `cybersiara`, `imperva`, `twitch_integrity` — now have real
`solve_http`/`solve_browser` paths (wire modules plus capture/verify flows). There are no
`not_implemented()` call sites and no `NotImplementedError` left under `src/captcha/solvers`.
The helper itself is now dead code.

All eight remain `experimental=True`; none has a canary fixture. Unit tests prove the wire and
error mapping (311 passed), not live success.

## 3. Previous blockers: what closed, what did not

_Status at `0f54c14`; current status is in Post-report fixes above._

| id | previous blocker | status now | evidence | residual |
|---|---|---|---|---|
| B1 | canary has no fixtures | **CLOSED (code/corpus)** | 10 tracked fixtures, `tests/canary/test_fixtures.py` pins the set, schema, solution keys and a fake-gateway run; compose mount target exists | one replay case per type, not L1; empty set still reports `pass_rate=1.0` and exit 0 (see §5) |
| B2 | `ALLOW_EXPERIMENTAL_TASKS` not implemented | **PARTIAL** | field exists (`config.py:28`), gateway filters experimental-only types (`gateway/app.py:133-153`), tests cover both values | default is still `True`; contract §7 requires `0`; not in `.env.example` or compose |
| B3 | heartbeat/capabilities browser-only and boot-time | **PARTIAL, with a live defect** | cv, protocol, http and browser all start `LaneHeartbeat`; `CAPTCHA_TYPES_REFRESH_SEC=20` refreshes the gateway set; `CAPTCHA_EXTRA_TYPES` exists; `/readyz` checks lanes | HTTP heartbeat advertises all 63 types and the gateway unions them back, so the browser gate is defeated; non-browser types are still sold from the registry without any live lane (details in §4) |
| B4 | netguard not wired to client URLs | **CLOSED (code/tests)** | `validate_task_urls()` runs in `routes/tasks.py:85` and `routes/legacy.py:154` before pricing/charge; `adapters/transport/guard.py` and `redirects.py` check every hop; `curl.Client` sends `allow_redirects=False`; gateway tests assert private/metadata/non-http URLs are rejected without charge | live redirect chains against real hosts are not exercised here |
| B5 | eight types are hard stubs | **CLOSED (code)** | real solvers + 311 tests; zero `not_implemented()` calls | all eight experimental, no canary fixture, no live evidence |
| B6 | 52/62 types have no runbook | **OPEN** | index is current: 37 covered / 26 pending, 63 rows, all enum values present | 26 pending; 7 lane labels in the index disagree with `catalog.lane_for` (see §5) |
| B7 | 0/62 evidenced at L3 | **OPEN** | no 7-day type-health history, no pass/p95/coverage record for any type | final gate remains 0/62 |
| B8 | gate docs stale | **PARTIAL** | some docs synced, but lanes, test count, canary fixture count and open-items text are still wrong (see §5) | docs-only PR needed |

## 4. Heartbeat gating: infrastructure works, the sale gate does not

The previous report's B3 is no longer "only browser beats". All four lanes beat:

- `worker_http/worker.py:111,183` (`HttpLaneHeartbeat`)
- `worker_cv/worker.py:165` (`LaneHeartbeat`, lane `cv`)
- `worker_protocol/worker.py:141` (`LaneHeartbeat`, lane `protocol`)
- `worker_browser/worker.py:200` (`LaneHeartbeat`, lane `browser`)

`LiveTypesRefresher` (`gateway/app.py:176-268`) re-reads `captcha:lane:*` and
`captcha:cap:*` every 20 s. `/readyz` returns 503 without a live lane
(`gateway/routes/health.py:38-64`). Unit tests cover cv/protocol beats, lease clearing,
refresh without restart, Redis faults, `CAPTCHA_EXTRA_TYPES`, and the browser-only helper.

Two gaps remain.

**4a. HTTP heartbeat defeats the browser gate (new finding).**
`worker_http/worker.py:124-132` builds the advertised set as every type that has any
`info.http` solver. Every type has one, including all 11 browser-only types, so the set is all
63. `CapabilityRegistry.live_types()` unions the cap sets of live lanes, and
`_available_types()` (`gateway/app.py:104-118`) adds `live_types` back after removing
browser-only types. With only an HTTP worker alive:

```
sold count with only an HTTP heartbeat: 63
browser-only sold anyway: ['atbcaptcha', 'captchafox', 'cf_challenge', 'hcaptcha',
 'hcaptcha_enterprise', 'prosopo', 'tencent', 'turnstile', 'turnstile_invisible',
 'yandex', 'yidun']
browser-only correctly withheld: []
```

The existing tests (`tests/gateway/test_capabilities_wiring.py`) call `_available_types` with an
empty or a browser-only live set; none exercises the real HTTP heartbeat set, so they pass while
the composed system sells browser types with no browser worker. Fix: derive HTTP caps from the
catalog lane (or subtract `_browser_only`) before writing `captcha:cap:http`, and add a test
that builds the caps through `Worker._http_capabilities()`.

**4b. Non-browser types do not need a heartbeat at all.**
`_available_types()` returns `(registry - browser_only) | live_types`, so an `http`, `cv` or
`protocol` type is sellable from the registry even when that lane has no live worker. Contract §2
says the gateway accepts a type only with a fresh heartbeat; contract §7 writes the union with
registry types. The code follows §7 for non-browser lanes. That ambiguity must be resolved by
lead, not silently by the implementation.

**4c. `ALLOW_EXPERIMENTAL_TASKS`.** The switch exists and is tested at both values, but
`Settings.allow_experimental_tasks` defaults to `True` (`config.py:28`) and
`worker_http/worker.py:144` still picks with `allow_experimental=True`. A gateway started with
`ALLOW_EXPERIMENTAL_TASKS=0` withholds the 38 experimental-only vendor types (24 remain
sellable), but the default deployment sells all 63. The variable is absent from
`.env.example` and `docker-compose.yml`. This is a deliberate beta divergence per the comment
at `config.py:25-27`, not a completed contract item.

## 5. Documentation drift found during verification

_Drift at `0f54c14`; the doc pass in Post-report fixes removed it. Line numbers refer to that snapshot._

| file:line | claim | fact |
|---|---|---|
| `README.md:32`, `README.md:48` | canary fixtures exist for 2 of 62 types | 10 of 62; see §2.3 |
| `README.md:35` | netguard is connected only to browser navigation; gateway boundary is work in progress | wired in `routes/tasks.py`, `routes/legacy.py`, transport guard and redirects; tests in `tests/gateway/test_netguard_urls.py` |
| `README.md:54-55` | lanes `http 21`, `cv 34` (+ hopscotch), `protocol 2`, `browser 5` | `http 25`, `cv 28` (+ hopscotch), `protocol 2`, `browser 8` |
| `README.md:148` | 1924 passed, 12 skipped | 2447 passed, 12 skipped |
| `AGENTS.md:109-110` | `http` 21, `cv` 34, `protocol` 2, `browser` 5 | 25 / 28 / 2 / 8 (including hopscotch) |
| `AGENTS.md:111-113` | 1924 passed | 2447 passed |
| `AGENTS.md:108` | 42 of 60 solvers = 38 of 62 vendor types | 42 solvers cover 44 type values; 38 is the experimental-only vendor count (43 have at least one experimental solver) |
| `docs/architecture.md:36-39,46` | lane table and sample output 21/34(+hopscotch)/2/5, total 35 cv | 25/28/2/8; sample output is stale |
| `docs/architecture.md:276-278` | fixtures for `friendly_captcha_v1` and `altcha_v1`, 2 types, other 60 have none | 10 fixtures / 10 types; 52 vendor types still have none |
| `docs/architecture.md:323-324` | 42 of 60 solvers = 38 vendor types plus hopscotch | 42 solvers cover 44 type values (43 vendor + hopscotch); 38 is experimental-only, not the experimental solver coverage |
| `docs/architecture.md:330-334` | heartbeat covers only browser; cv/protocol do not beat; `CAPTCHA_EXTRA_TYPES` does not exist; live set read once at boot | all four lanes beat; extra types and 20 s refresh exist; the real remaining problem is the HTTP cap leak (§4a) |
| `docs/architecture.md:348-352` | netguard is not on the gateway boundary; only browser navigation calls it; `feat/netguard-http` not merged | gateway + legacy + transport call it; branch merged in `db9ba1d` |
| `docs/architecture.md:356-358` | `not_implemented()` remains only in botdeflector | zero call sites; the helper at `_base.py:161` is dead |
| `docs/plan.md:29` | lanes `http` 20, `cv` 38, `protocol` 2, `browser` 2 | vendor split 25/27/2/8 (25/28/2/8 including hopscotch) |
| `docs/plan.md:120-121` | 58 load without cv, 60/62 types | 60 load, 63/63 types |
| `runbooks/README.md` type table | lane column | 7 rows disagree with `catalog.lane_for`: `funcaptcha`, `funcaptcha_enterprise` (index cv, actual http); `tencent`, `yidun`, `atbcaptcha` (index cv, actual browser); `alibaba`, `hunt` (index cv, actual http) |
| `src/captcha/config.py:26` | 41 experimental solvers | 42 |

`docs/operations.md:58-61` correctly records that `.env.example` lacks
`ALLOW_EXPERIMENTAL_TASKS`; that gap is still present. `docs/baseline.md` is an explicitly
historical snapshot and is not counted as drift here.

## 6. Blockers to 62/62 L3

Ordered by what stops the final gate first.

1. **No type has L3 evidence.** 0/62 have a 7-day canary, pass/p95/coverage record, or seven
   days of `captcha_type_health=1`. This is the gate itself, not a code bug.
2. **Browser capacity is not actually gated.** §4a: one HTTP worker makes all 63 types sellable,
   including the 11 browser-only types. Until fixed, browser pass rates and capacity assumptions
   cannot be trusted.
3. **52/62 types have no canary fixture at all**, and the 10 that exist are single replay cases,
   below every plan §2 L1 family count. There is no path to L1/L2/L3 for the other 52 from this
   tree.
4. **`ALLOW_EXPERIMENTAL_TASKS` still defaults to `True`.** Contract §7 requires `0`; 38
   vendor types are experimental-only. The gate exists but is not armed.
5. **38 experimental-only vendor types still carry unverified wire.** 56 `# FILL:` markers,
   including field names/order/encoding for antibot engines. The eight former stubs are
   implemented, but implementation is not live verification.
6. **CV types have no weights.** Zero `*.safetensors` in the tree, empty `models-cache/`; only
   6 grid, 2 rotate, 4 detection and 2 slider golden images. Classification/detection/rotate
   answer `ERR_CONFIG` without weights, so they cannot pass even offline L1.
7. **26 runbooks remain pending**, and the index has 7 wrong lane labels. Plan §1 treats a
   runbook (or explicit family coverage) as part of "type closed".
8. **Gate docs are stale** (see §5). A green claim that cites 1924 tests or 21/34/2/5 lanes is
   not evidence for this tree.

## 7. What cannot be closed without live accounts/proxies

These cannot be signed off in this worktree, by any amount of local testing:

- **L1 golden sets for live families.** Recorded live captures per type (>=100 labelled cases,
  >=300 for grid, 400 blind for hopscotch). The repository has replay fragments, not corpora.
- **L2 staging canary.** 6 h per type, >=3 consecutive runs against a staging gateway with real
  solving and egress; needs accounts, proxies and a staging deployment.
- **L3 7-day production canary.** Pass rate, coverage, p95 and seven days of
  `captcha_type_health=1` per type. This is a production observation, not a repo artifact.
- **Wire constants for the 38 experimental types.** The 56 `# FILL:` sites (Akamai, CF,
  DataDome, Kasada, PX, Imperva, Castle, TSPD, CyberSiARA, Twitch, BotGuard, BotDeflector,
  interactive vendors) require live captures and often an exit IP in the target country.
- **hCaptcha browser / Steam / Riot / Epic.** Camoufox is not installed, no proxy, no account;
  the `docs/plan.md` §5 measurement is a recorded jsdom probe, not a browser-lane pass rate.
- **Proxy-dependent pass rates and IP rotation.** No proxy pool in the worktree
  (`data/` is off-limits and empty by policy); antibot pass rates are meaningless on one host IP.
- **Browser CI job.** Needs a Camoufox image/download and network; not run here.

Everything else in §6 (gate arming, heartbeat caps, docs, runbooks, fixtures wiring, CI) is
closable offline.

## 8. Recommendations

1. **Fix the heartbeat leak first.** Build `_http_capabilities()` from types whose catalog lane
   can run on HTTP and which are not `_browser_only`; add a test that feeds the real HTTP caps
   into `_available_types` and asserts every browser-only type is withheld. Then decide §4b:
   either gate every type on its lane's live cap set (contract §2) or document the §7 registry
   union as the intended rule.
2. **Arm the experimental gate.** Default `ALLOW_EXPERIMENTAL_TASKS=False`, add it to
   `.env.example` and the compose env, update the test that currently asserts the beta default,
   and keep `1` only as an explicit beta override. This alone reduces the sellable set to the
   24 vendor types with a non-experimental solver.
3. **Sync the docs with one inventory run.** Update the lane counts, test count, canary fixture
   count, heartbeat/netguard open items and the dead `not_implemented` note in
   `README.md`, `AGENTS.md`, `docs/architecture.md`, `docs/plan.md` and
   `runbooks/README.md`. Docs-only PR; the numbers are in §2.
4. **Make the canary fail closed.** `SuiteReport.pass_rate` is 1.0 for zero fixtures
   (`services/canary/suite.py:74`) and `--once` returns 0; a production run with an empty set
   must exit non-zero. Keep `test_fixture_dir_is_not_empty` as the CI check.
5. **Grow the corpus from recorded artefacts, labelled by evidence class.** Add the 52 missing
   types at replay-fixture level now, mark each note "replay, not L3", then replace with live
   captures family by family. Add per-type minimum counts to CI so an empty/low set cannot read
   as green.
6. **Close the runbook tail and the index lanes.** 26 pending files; fix the 7 lane labels.
   Family runbooks are acceptable only if the index says so explicitly.
7. **Delete the dead helper and stale open-item text** in `solvers/antibot/_base.py:161` and
   `docs/architecture.md` `"Что ещё не сделано"` items 1, 5, 7.
8. **Start the L3 clock only after 1-7.** One type at a time, 7 days, recorded pass/p95/coverage
   and `captcha_type_health`; flip `experimental=False` only on that evidence.
9. **Keep the gate commands in CI.** The unit suite is already CI-scoped
   (`.github/workflows/ci.yml:48`, integration/browser are separate jobs); add
   `docker compose config --quiet` and a docs relative-link check so stale counts and broken
   links fail the same way tests do.

## 9. Reproduction

```powershell
# environment: worktree has no .venv/uv.lock and the box is offline
$env:UV_PROJECT_ENVIRONMENT='D:/captcha/.venv'
$env:PYTHONPATH='D:/wt/captcha-verifier2/src'

uv run --no-sync pytest -q
uv run --no-sync ruff check src tests
uv run --no-sync lint-imports
docker compose -f docker-compose.yml config --quiet

# targeted contract tests
uv run --no-sync pytest -q tests/adapters/test_capabilities.py tests/adapters/test_netguard.py `
  tests/adapters/test_netguard_shape.py tests/adapters/test_transport_netguard.py `
  tests/adapters/test_transport_redirects.py tests/gateway/test_capabilities_wiring.py `
  tests/gateway/test_netguard_urls.py tests/services/test_experimental_gate.py `
  tests/services/test_heartbeat_lanes.py tests/canary/test_fixtures.py

# inventory
python -c "from captcha.solvers.registry import discover_solvers; ..."
python -c "from captcha.domain.enums import CaptchaType; from captcha.domain.catalog import lane_for; ..."
```

The inventory/gap scripts used for this report were kept outside the repo (Windows temp);
they import `captcha` with `PYTHONPATH=<worktree>/src` and make no network calls.

## 10. Bottom line

The four mechanical gates are green and the local inventory is healthy: 63 types, 60 solvers,
63/63 coverage, the expected 25/28/2/8 lanes, 10 canary fixtures, and no hard stubs. Three of
the previous five code blockers (canary corpus, netguard wiring, eight stubs) are closed in
code. The final gate is still **0/62 at L3**, and two correctness gaps remain before the tree is
even safe to measure: browser types are sold without a browser worker because the HTTP heartbeat
advertises them, and the experimental gate defaults to selling all 62 vendor types. Both are
offline fixes; the L1/L2/L3 evidence and the wire constants are not.
