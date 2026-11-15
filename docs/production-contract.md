# Production contract (W0, frozen)

Этот файл — закон для агентов W1. Менять его может только lead. Если реализация требует отступления — сначала сообщение lead, потом код.

## 1. Scope

- **Все 62 типа** из `domain/enums.py` — в финальную приёмку. Ни один тип не выкидывается.
- **Hopscotch** — дополнительный custom-тип `CaptchaType.HOPSCOTCH`, не входит в 62.
- **REST only**: `/createTask`, `/getTaskResult`, `/getBalance`, `/in.php`, `/res.php`, `/account/*`, `/admin/*`. HTML/шаблоны/статика не добавляются.
- **Финальный гейт**: 62/62 L3 + hopscotch 95%/80% (или beta с abstention по решению lead).

## 2. Lanes и modes

~~~python
Lane = Literal["http", "cv", "protocol", "browser"]
class SolveMode(StrEnum): HTTP; BROWSER; AUTO
~~~

- Тип по умолчанию идёт в свой lane из `domain/catalog.py`.
- `NeedsBrowserError` (`ERR_NEEDS_BROWSER`) — HTTP-солвер просит browser lane. Worker-http перекладывает конверт в `browser` stream, ack'ает исходное сообщение, не списывает повторно.
- `TaskEnvelope.lane_hops` — максимум 1. Второй `NeedsBrowser` в browser lane = terminal `ERR_UNSOLVABLE`.
- Heartbeat: `SETEX captcha:lane:<lane>:<worker> 90 1`, `SADD captcha:cap:<lane> <types>`; gateway принимает тип только при свежем heartbeat. `/readyz` проверяет Redis, PG и heartbeat.

## 3. Solver ABI

~~~python
class SolverInfo:
    name: str
    types: tuple[CaptchaType, ...]
    http: bool = True
    browser: bool = False
    needs_js_runtime: bool = False
    needs_gpu: bool = False
    experimental: bool = False
    version: str = "0.1.0"

class Solver:
    async def prepare(ctx): ...
    async def shutdown(): ...
    async def solve_http(task, ctx) -> SolverResult: ...
    async def solve_browser(task, ctx) -> SolverResult:
        raise CaptchaError(...)   # default
    def supports(mode: SolveMode) -> bool: ...
~~~

- `SolverContext.browser` — провайдер сессии (не сырой Playwright).
- Солвер не создаёт HTTP-клиент, прокси, модель или браузер сам: только через `ctx`.
- Солверы не импортируют `captcha.adapters` (кроме разрешённых двух text-солверов).

## 4. Browser provider

Интерфейс (реализует `adapters/browser/provider.py`):

~~~python
class BrowserProvider(Protocol):
    @asynccontextmanager
    async def session(self, spec: BrowserSpec) -> AsyncIterator[BrowserSession]: ...
~~~

`BrowserSpec`: `proxy: str | None`, `cookies: list[dict]`, `user_agent: str | None`, `locale`, `timezone`, `headless`, `label`.

- **Одна свежая сессия на задачу**, не общий контекст между клиентами.
- Camoufox-опции: `os="windows"`, `geoip=<exit_ip>`, `block_webrtc=True`, `enable_cache=False`, `firefox_user_prefs=FIREFOX_PREFS`, `exclude_addons=[DefaultAddons.UBO]`, `persistent_context=True`, `user_data_dir=<temp>`, `headless`.
- GeoIP через exit-IP прокси; без exit-IP — `ProxyError`, не host-fallback.
- Concurrency: `WORKER_BROWSER_SESSIONS=12` (16 ГБ, тюнинг 8–20 по RSS); `WORKER_BROWSER_MIN_FREE_MB=2048`.
- Per-task hard timeout 180–300 с; kill дерева процессов; reaper профилей `<temp>/captcha-browser-*`; ноль orphan.

## 5. Ошибки и retry

- `CaptchaError` subclasses: `UnsolvableError`, `ConfigError`, `ProxyError`, `TimeoutError`, `JSRuntimeError`, `ModelInferenceError`, `ChallengeExpiredError`, `SensorError`.
- Новые: `NeedsBrowserError` (`ERR_NEEDS_BROWSER`), `QuotaError` (`ERR_QUOTA`), `UrlRejectedError` (`ERR_URL_REJECTED`).
- Terminal: `ERR_UNSOLVABLE`, `ERR_CONFIG`, `ERR_URL_REJECTED`. Остальные — retry/DLQ по политике Consumer.
- Не логировать payload/cookies/proxy/token; все ошибки через redaction.

## 6. Task payload (2captcha compat)

- `type` принимает snake_case и официальные имена: `HCaptchaTaskProxyless`, `RecaptchaV2TaskProxyless`, `TurnstileTaskProxyless`, `FunCaptchaTaskProxyless`, `GeeTestTaskProxyless`, `ImageToTextTask` и proxy-варианты.
- Прокси: `proxyType/proxyAddress/proxyPort/proxyLogin/proxyPassword` → `Task.proxy`; `proxy` строкой; legacy `proxytypeaddr`.
- hCaptcha: `isInvisible`/`invisible`, `enterprisePayload`/`rqdata`, `cookies`, `host`, `userAgent`.
- Hopscotch custom: `{image_b64, frames?, hint?}` (1000/1600×400, 5/8, ≤32 МБ) → `{index, coordinates}`.
- `Task.payload` в Postgres санитизируется: без `proxy*`, `cookies`. Полный конверт — только Redis TTL 24ч.

## 7. Capabilities и gateway

- `state.solver_types` = registry types ∪ `CAPTCHA_EXTRA_TYPES` ∪ heartbeat cap sets.
- Если типа нет в capabilities — `ERROR_TASK_NOT_SUPPORTED`, без списания.
- `ALLOW_EXPERIMENTAL_TASKS=0` по умолчанию: experimental-типы не продаются.

## 8. Security

- `netguard` для `pageurl`, `websiteURL`, `challenge_url`, `surl`, `api_server`: only http/https, resolve A/AAAA, deny private/loopback/link-local/ULA/multicast/metadata, no redirects to private, max length 2048.
- API keys: hash at rest (SHA-256 + pepper), `key_tail`; migration.
- Rate limit/quota: `RATE_LIMIT_CREATE_PER_MIN`, `MAX_ACTIVE_TASKS_PER_ACCOUNT`, `MAX_DAILY_TASKS`, `MAX_DAILY_SPEND`.
- DLQ: redacted envelope.
- TLS ingress; `ADMIN_TOKEN` обязателен.

## 9. ML artifacts

- 10 artifacts `docs/ml-tz.md` + `hopscotch-v1`.
- `hopscotch-v1`: `model.safetensors` + `config.json` + `release.json` (calibration/rejector), DINOv3 ViT-B/16.
- `make publish-model` / `make check-model`; `latest` rollback.
- Unified visual core: DINOv3/SigLIP2 + heads (OCR, grid, detection, geometry, matching). ADR decides; per-type fallback allowed.
- Тип получает `experimental=False` только после L3 canary; verifier фиксирует.

## 10. Metrics / canary

- `captcha_tasks_total{type,lane,status}`, `captcha_task_latency_seconds{type,mode}`, `captcha_queue_depth{lane}`, `captcha_browser_sessions_active`, `captcha_browser_launch_seconds`, `captcha_browser_rss_bytes`, `captcha_escalations_total`, `captcha_canary_pass_rate{type}`, `captcha_type_health{type}`, `captcha_dlq_total`.
- Canary 6ч; pass/p95; type health; alert webhook.
- L3 thresholds: see plan §2. Verifier sets `experimental=False` only after 7-day canary.

## 11. Ownership W1 (one file — one owner)

| Owner | Files |
|---|---|
| api-compat | `services/gateway/type_map.py`, `schemas.py`, `legacy_map.py`, gateway tests |
| browser-core | `adapters/browser/*`, `adapters/netguard.py`, `domain/ports.py` |
| worker-browser | `services/worker_browser/*`, config browser fields |
| hcaptcha-browser | `solvers/hcaptcha/browser.py`, `sites.py` |
| hcaptcha-http | `solvers/hcaptcha/http.py`, `adapters/hcaptcha_js/*`, probe |
| router-escalation | `domain/errors.py`, `domain/task.py`, `queue/streams.py`, `worker_http/worker.py`, `solvers/registry.py` |
| security | `gateway/auth.py`, `storage/repos.py`, `routes/tasks.py`, redaction/quotas |
| ml-platform | `adapters/weights/*`, `services/pipeline/*` |
| pow-text-ocr | `solvers/pow/*`, `solvers/ocr/*`, `solvers/audio/*` |
| cv-grid | `solvers/classification/*` |
| observability | `observability/*`, `gateway/routes/health.py`, `services/canary/*` |
| ci-tests | `.github/*`, tests/integration, tools/load |
| infra-deploy | `infra/*`, `docker-compose.yml`, Caddy |
| docs | `docs/*`, `runbooks/*` |
| research-sites | `tools/hcaptcha_probe.py`, `docs/hcaptcha-sites.md` |
| ml-research | `docs/ml-research.md`, `docs/adr-vision-architecture.md`, `tools/ml_probe/*` |
| hopscotch-ml | `ml/hopscotch/*`, `solvers/hopscotch/*`, `docs/ml-hopscotch.md` |

## 12. Merge rules

- Worktree `D:/wt/captcha-<role>`, branch `feat/<role>` from `feat/prod-base`.
- No push to main, no self-merge, no edits to `.env`, `data/`, keys, `D:/project`, `D:/riot-research`, `D:/hopscotch_v2` (read/copy only).
- Tests + ruff for touched scope; live/canary only through verifier.
- Lead merges 1→2→…→17; after each `make check`.
