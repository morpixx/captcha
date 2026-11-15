# Runbook: canary

> `services/canary` — единственный механизм, который измеряет тип **в проде**, а не в
> тестах. Гейт L3 (`docs/plan.md` §2) опирается на его отчёт, поэтому «канарейка зелёная»
> обязана означать «были фикстуры и они прошли», а не «фикстур не было».

## 1. Snapshot

- **процесс:** `python -m captcha.services.canary` (демон) или `… --once` (один прогон).
- **расписание:** `CANARY_INTERVAL_SEC=21600` (6 ч), apscheduler, `max_instances=1`,
  `coalesce=True`; на старте демон делает один прогон сразу.
- **что делает:** POST `/createTask` → опрос `/getTaskResult` → pass rate и p50/p95 →
  `captcha_canary_pass_rate{type}`, `captcha_type_health{type}` →
  `captcha:typehealth:<type>` (TTL 12 ч) → webhook, если ниже порога.
- **код:** `services/canary/{cli,suite,alerts,scheduler}.py`.

## 2. Фикстуры

Каталог: `CANARY_FIXTURES_DIR`, по умолчанию `tests/canary/fixtures` (в репозитории его
**пока нет** — см. §3). Один JSON на кейс:

~~~json
{
  "name": "hcaptcha-riot-login",
  "clientKey": "<canary-account-key>",
  "task": {
    "type": "HCaptchaTaskProxyless",
    "websiteURL": "https://authenticate.riotgames.com",
    "websiteKey": "<sitekey>"
  },
  "expect": { "status": "ready", "solution_contains": ["gRecaptchaResponse", "userAgent"] }
}
~~~

- `clientKey` обязателен по смыслу: прогон идёт через настоящий gateway и списывает деньги.
  Отдельный аккаунт с лимитами — правильная практика.
- `expect.status`: `ready` (дефолт) или `failed`, если кейс проверяет корректный отказ.
- `expect.solution_contains`: список ключей решения. Для типов с обязательным ключом
  (`gRecaptchaResponse` у hCaptcha, `token` у Turnstile, `cookies` у cookie-движков)
  это главная проверка формы ответа.
- Прогон последовательный (`for` по фикстурам): канарейка не load-test. p95 считается по
  всем кейсам прогона, а не по типу.
- Полинг: `CANARY_POLL_INTERVAL_SEC=5`, бюджет кейса `CANARY_TASK_TIMEOUT_SEC=180`.

## 3. Пустой набор — это не «зелено»

`run_once()` на пустом каталоге возвращает отчёт с `total=0` и `pass_rate=1.0`;
`--once` выходит с кодом 0; алерт уходит только как `reason=no_fixtures` и только если
`CANARY_ALERT_WEBHOOK` задан. Практические правила:

1. держать `tests/canary/fixtures` непустым и версионируемым (без секретов: ключ — из env
   или из отдельного файла, не в git);
2. в CI проверять `python -m captcha.services.canary --once` **и** `report.total > 0`;
3. «L3 пройден» без `total > 0` за 7 дней — не пройден.

## 4. Запуск

~~~bash
# один прогон (CI / после деплоя)
uv run python -m captcha.services.canary --once

# демон
CANARY_INTERVAL_SEC=21600 CANARY_ALERT_WEBHOOK=https://… \
CANARY_FIXTURES_DIR=tests/canary/fixtures \
  uv run python -m captcha.services.canary

# переопределить цель
uv run python -m captcha.services.canary --once --gateway http://127.0.0.1:8080 --fixtures ./canary
~~~

Нужны Redis (type health) и живой gateway. Без Redis прогон валиден, но вердикт по типам не
публикуется. `--once` возвращает 1, если pass rate ниже порога, и 0 иначе.

## 5. Чтение результата

- Лог `canary.run.completed`: `total, passed, failed, pass_rate, p50_ms, p95_ms`.
- Лог `canary.fixture.failed`: `name, type, status, error, latency_ms` — по одной строке на
  провал; `status` бывает `transport_error`, `http_<code>`, `bad_json`,
  `create_rejected`, `processing`/`failed` (не совпало с `expect`).
- `captcha:typehealth:<type>` = `ok`/`degraded`; `captcha_type_health{type}` = 1/0.
- Webhook-payload: `{source, reason, hostname, gateway, threshold, report}`; `reason` —
  `no_fixtures` или `pass_rate_below_threshold`.

Порог алерта `CANARY_PASS_THRESHOLD=0.9` — **операционный**, не приёмочный. L3 требует 0.95
по типу 7 дней подряд (`docs/plan.md` §2.4); тип с 0.92 не будит дежурного, но L3 не проходит.

## 6. Failure modes

| симптом | причина | что делать |
|---|---|---|
| `canary.fixtures.missing` | каталога нет | создать `tests/canary/fixtures`, положить кейсы |
| `create_rejected` c `ERROR_TASK_NOT_SUPPORTED` | тип не в реестре этого деплоя | проверить `/admin/solvers`, лейн, `--extra cv` |
| `create_rejected` c `ERROR_NO_SLOT_AVAILABLE` | квоты канареечного аккаунта | поднять лимиты или разнести кейсы по времени |
| `create_rejected` c `ERROR_ZERO_BALANCE` | кончились деньги на canary-аккаунте | пополнить |
| `transport_error` | gateway недоступен/таймаут 15 с | `/readyz`, сеть, балансировщик |
| `processing` до дедлайна | задача не успела за 180 с | тип медленный (browser?) — поднять `CANARY_TASK_TIMEOUT_SEC` до 300 |
| `failed` вместо `ready` | тип реально сломан | `runbooks/<type>.md` §6 |
| solution missing keys | форма ответа поехала | `docs/protocol.md`, `domain/solution.py` |
| `pass_rate=1.0` и `total=0` | пустой набор | §3 |

## 7. Как добавить тип в канарейку

1. Взять один живой кейс (sitekey/URL/картинка), который стабильно решается вручную.
2. Сохранить JSON по формату §2; `solution_contains` — ключи из `docs/protocol.md`.
3. Прогнать `--once --fixtures <каталог>` локально.
4. Добавить в `tests/canary/fixtures`, закоммитить **без** clientKey (он из env/секрета).
5. Для L3 нужно 7 дней прогонов; один зелёный прогон — это L2.

## 8. Гигиена

- Канареечный аккаунт изолирован: отдельный ключ, лимиты, бюджет. Его падение не должно
  блокировать клиентов.
- Фикстуры не содержат cookies/прокси/ключей; если кейс требует cookies — они живут в
  секрете, подставляемом перед прогоном.
- Webhook — только URL, без токена в query; тело не содержит решения, только метрики и
  имена кейсов.
- Один экземпляр канарейки. Два демона = двойной расход и искажённый p95.

## 9. Incident log

- `2026-11-15 — initial setup — сервис собран, фикстур в репозитории нет; «зелёная»
  канарейка на пустом наборе — известный риск (см. §3).`

## 10. References

- Код: `src/captcha/services/canary/`
- Метрики: `src/captcha/observability/metrics.py` (`canary_pass_rate`, `type_health`)
- Контракт: `docs/production-contract.md` §10; гейт: `docs/plan.md` §2
- Эксплуатация: `docs/operations.md` §4, §8
