# Runbook: browser lane

> Операционный runbook лейна `browser`, а не одного типа. Тип, который в нём живёт,
> описан в `runbooks/hcaptcha.md`. Контракт: §2 (лейны, один прыжок), §4 (провайдер,
> 12 сессий, 180–300 с, kill дерева процессов), §10 (метрики).

## 1. Snapshot

- **lane:** `browser`; типы по каталогу: `hcaptcha`, `hcaptcha_enterprise`, `prosopo`,
  `captchafox`, `yandex`.
- **worker:** `src/captcha/services/worker_browser/` — `cli.py`, `worker.py`, `runner.py`,
  `gate.py`, `heartbeat.py`, `limits.py`, `tracking.py`.
- **provider:** `adapters/browser/provider.py` (`CamoufoxProvider`), опции —
  `adapters/browser/prefs.py`, процессы/профили — `supervisor.py`, GeoIP — `geoip.py`.
- **запуск:** `python -m captcha.services.worker_browser --consumers 1` (в compose сервиса
  нет — `runbooks/deploy.md`).
- **владелец:** worker-browser (воркер, gate, heartbeat), browser-core (провайдер,
  supervisor, netguard), hcaptcha-browser (солвер, профили сайтов).

## 2. Конфигурация

| переменная | дефолт | смысл |
|---|---|---|
| `WORKER_BROWSER_SESSIONS` | 12 | потолок одновременных сессий на процесс |
| `WORKER_BROWSER_RAMP_START` | 2 | с какого числа сессий стартовать |
| `WORKER_BROWSER_MIN_FREE_MB` | 2048 | ниже этой свободной RAM новые сессии не берутся |
| `WORKER_BROWSER_TASK_TIMEOUT_SEC` | 240 | hard timeout задачи, клампится в 180–300 |
| `WORKER_BROWSER_HEADLESS` | 1 | headless; для отладки — `0` |
| `WORKER_BROWSER_CONSUMERS` | 1 | consumers на процесс; `min(consumers, sessions)` |

Константы лейна в коде: `DEFAULT_CONCURRENCY=12`, `MIN_TIMEOUT_SEC=180`,
`MAX_TIMEOUT_SEC=300`, `CANCEL_GRACE_SEC=5`, терминальные коды
`{ERR_UNSOLVABLE, ERR_CONFIG, ERR_URL_REJECTED, ERR_NEEDS_BROWSER}`
(`worker_browser/limits.py`).

Зависимости: `camoufox` (браузер скачивается при первом запуске), `psutil` (дерево
процессов). Playwright напрямую не используется — контракт держится на `BrowserProvider`.

## 3. Жизнь одной задачи

1. consumer читает `captcha:stream:browser`, конверт получает `lane_hops`;
2. gate берёт слот (семафор + RAM-guard + ramp);
3. `TrackingProvider.session(spec)` → `CamoufoxProvider.session`:
   exit-IP прокси → GeoIP, временный профиль `<temp>/captcha-browser-*`, один запуск
   Camoufox, cookie-jar задачи; метрика `captcha_browser_launch_seconds`;
4. `solve_browser(task, ctx)` под `asyncio.wait(timeout)`;
5. на таймауте: cancel + `tracker.kill_label` (всё дерево процессов) + 5 с на unwind;
6. закрытие сессии: kill дерева, удаление профиля, `captcha_browser_sessions_active` −1;
7. `set_ready`/`set_failed`; `ERR_NEEDS_BROWSER` здесь терминален — второй прыжок запрещён;
8. метрики: `captcha_tasks_done_total`, `captcha_task_latency_seconds{solve_mode="browser"}`,
   периодически `captcha_browser_rss_bytes` (не чаще раза в 15 с).

## 4. Метрики и алерты

| метрика | здоровое поведение | тревога |
|---|---|---|
| `captcha_browser_sessions_active` | растёт до 12 и держится | 0 при входящем трафике — воркер/провайдер мёртв |
| `captcha_browser_launch_seconds` | 1–3 с p95 | > 10 с — диск/CPU/антивирус, холодный старт |
| `captcha_browser_rss_bytes` | ~700 МБ × сессии | > 80% RAM ноды — резать `SESSIONS` |
| `captcha_queue_depth{lane="browser"}` | < 100 | растёт — сессии не освобождаются |
| `captcha_escalations_total{type}` | ноль для браузерных типов | рост по HTTP-типам — деградация HTTP-пути |
| `captcha_solver_errors_total{type}` | `ERR_TIMEOUT` редок | `ERR_PROXY`/launch-ошибки пачкой |

Логи: `browser.session.start/end` (debug), `gate.memory_low/ramp_up`, `task.hard_timeout`
с `killed=<n>`, `browser.provider.closed`.

## 5. Проверки перед выкаткой

1. `python -m captcha.services.worker_browser --help` — процесс импортируется без camoufox;
2. на ноде: `python -c "import camoufox"` и пробный запуск `--no-headless` с одной задачей;
3. `/readyz` показывает `browser` в `live_lanes` после старта (heartbeat 30 с, TTL 90 с);
4. `captcha_browser_sessions_active` появляется при первом трафике;
5. `<temp>` не содержит старых `captcha-browser-*` после прогона.

## 6. Failure modes

| симптом | причина | что делать |
|---|---|---|
| `ERR_CONFIG: camoufox is not installed` | пакет не установлен в образе | установить camoufox; браузер скачается при первом запуске |
| `browser launch failed` / `camoufox launch failed` | нет дисплея/библиотек, нет места, битый профиль | запустить с `--no-headless` на ноде, проверить `/tmp` и свободное место |
| `ERR_PROXY: could not resolve the proxy exit IP` | прокси мёртв или DNS режет | сменить прокси; host-fallback отключён осознанно |
| `task.hard_timeout`, `killed=0` | браузер не найден по profile-маркеру | проверить имена процессов (`camoufox`, `firefox`) и права psutil |
| профили копятся в `<temp>` | процесс убит до cleanup, reaper не дошёл | `reap_stale` при следующем старте; вручную удалить `captcha-browser-*` после проверки, что процессов нет |
| `sessions_active` = 0, gate не пускает | `MIN_FREE_MB` не достигается | освободить RAM или снизить guard; лог `gate.memory_low` |
| все задачи `ERR_TIMEOUT` | прокси/страница медленные, таймаут 240 с | смотреть launch-время и p95; поднять timeout до 300 только осознанно |
| `ERR_UNSOLVABLE` после эскалации | виджет не дал токен | `runbooks/hcaptcha.md` §7 |

## 7. Ёмкость и тюнинг

- 16 ГБ → 12 сессий; 8 ГБ → 6 сессий и `MIN_FREE_MB=1024`. Больше 20 сессий на ноду не
  имеет смысла: упираешься в CPU и в размер профилей.
- `--consumers` больше 1 не увеличивает число сессий: `concurrency` делится на consumers.
- Браузерные задачи дорогие: `--max-retries 2` (дефолт CLI), а не 3 как в других лейнах.
- SIGTERM: чтение останавливается, in-flight добиваются, оставшиеся сессии убиваются через
  supervisor. Жёсткий `kill -9` оставляет профили — их снесёт reaper следующего процесса.

## 8. Отладка на ноде

~~~bash
# один consumer, headful, debug-логи
WORKER_BROWSER_SESSIONS=1 WORKER_BROWSER_HEADLESS=0 LOG_LEVEL=DEBUG \
  uv run python -m captcha.services.worker_browser --consumers 1 --no-headless
~~~

~~~powershell
# проверить, что профили и процессы не остаются
Get-ChildItem $env:TEMP -Filter 'captcha-browser-*'      # Windows
Get-Process camoufox,firefox -ErrorAction SilentlyContinue
~~~

## 9. Incident log

- `2026-11-15 — initial setup — лейн собран: провайдер, gate, heartbeat, kill дерева.
  Worker-browser не в compose; живой RSS на целевых сайтах не измерен (baseline, Known
  unknowns).`

## 10. References

- Контракт: `docs/production-contract.md` §2, §4, §5, §10
- Архитектура: `docs/architecture.md` («Browser lane», «Capabilities и heartbeat»)
- Код: `src/captcha/adapters/browser/`, `src/captcha/services/worker_browser/`
- Эксплуатация: `docs/operations.md` §7; выкатка: `runbooks/deploy.md`
- Canary по типам: `runbooks/canary.md`
