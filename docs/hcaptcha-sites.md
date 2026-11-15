# hCaptcha sites: live capture, HTTP pass rate, verdicts

Снимок 2026-11-15, 02:20 Europe/Berlin. Инструмент — `tools/hcaptcha_probe.py`, он же источник всех чисел ниже.

Задача файла — ответить на один вопрос по каждому хосту: **можно ли получить токен по HTTP**
(checksiteconfig -> n-token -> getcaptcha), или нужен браузер. Не «какой солвер лучше», а
«какой lane вообще имеет шанс».

## Метод

Один прогон = один task по контракту (`docs/production-contract.md` §2):

1. `POST /checksiteconfig?v=<build>&host=&sitekey=&sc=1&swa=1&spst=1` — новый HTTP-клиент
   (`curl_cffi`, impersonate `chrome131`), session-per-run;
2. n-token = hsw/hsj bundle (`adapters/hcaptcha_js/server.js`, jsdom + canvas) на challenge JWT;
3. `POST /getcaptcha/<sitekey>` — форма или msgpack+Ext(18), если сайт просит `enc_get_req`.

Итог прогона: `pass` (P1_ / pass UUID), `challenge` (сервер вернул новый `c` вместо токена),
`reject` (pass=false и без challenge), `error` (транспорт/протокол/бандл).

Команда:

~~~powershell
D:/captcha/.venv/Scripts/python.exe tools/hcaptcha_probe.py probe --runs 10 --interval 1.0 --node-path D:/riot-research/node_modules --out C:/Users/Public/hcaptcha-probe-run.json
~~~

50 прогонов (5 сайтов x 10), последовательно, один egress IP, без прокси. `client_version` —
`wire.DEFAULT_VERSION`, UA — `wire.DEFAULT_UA` (Chrome 131 / Win64). Node 26.13.0, jsdom 29.1.1,
canvas 3.2.3 (взяты из `D:/riot-research/node_modules`, read-only; `npm ci` в адаптере не
запускался — см. «Воспроизвести»).

## Матрица

| host | sitekey | где живёт | HTTP прогонов | pass | challenge | pass rate | p50 / p95, ms | вердикт |
|---|---|---|---|---|---|---|---|---|
| `store.steampowered.com` | `e18a349a-46c2-46a0-87a8-74be79345c92` | `/join/`, XHR `/join/refreshcaptcha/` | 10 | 0 | 10 | 0% | 798 / 3839 | browser |
| `steamcommunity.com` | `e18a349a-46c2-46a0-87a8-74be79345c92` | тот же ключ, виджета на `/login/home/` нет | 10 | 0 | 10 | 0% | 910 / 1981 | browser |
| `account.riotgames.com` | `b3c619fc-4b72-4838-9bf2-398888588d62` | Riot OAuth/account (D:/riot-research) | 10 | 0 | 10 | 0% | 761 / 1154 | browser |
| `authenticate.riotgames.com` | `a5f74b19-9e45-40e0-b45d-47ff91b7a6c2` | Riot authenticate, feature `andint` | 10 | 0 | 10 | 0% | 994 / 2177 | browser |
| `example.com` (контроль) | `10000000-ffff-ffff-ffff-000000000001` | тестовый ключ hCaptcha (passive) | 10 | 10 | 0 | 100% | 870 / 2306 | http |
| `www.epicgames.com` | — | hCaptcha не найдена, виджет — Talon | — | — | — | — | — | вне матрицы |

Вердикты: **http** — HTTP-лейн выдаёт токен; **browser** — HTTP доходит до challenge и токена не
получает, нужен браузер (и, если сайт показывает картинку, ответ на неё); **auto** — данных нет,
профиль остаётся `SolveMode.AUTO` и решает browser-лейн.

Стадии (p50, ms), те же прогоны:

| site | checksiteconfig | n-token | getcaptcha | длина n-token |
|---|---|---|---|---|
| steam-join | 402 | 103 | 224 | 15 068 … 39 012 (медиана 38 736) |
| steam-community | 506 | 91 | 255 | 38 400 … 39 428 |
| riot-account | 505 | 16 | 190 | 13 676 … 13 740 |
| riot-authenticate | 749 | 15 | 198 | 13 680 … 13 684 |
| hcaptcha-test-key | 613 | — (не нужен) | 151 | — |

Первый прогон каждого сайта дороже остальных на 1-3 с: поднимается jsdom-realm и тянется bundle;
дальше realm переиспользуется (как в worker'е).

## Что значит 0/10

`challenge` — сервер принял запрос и вернул **новый** `c` (`type: hsw`, `req`) вместо pass-токена.
Ответ с `error-codes` и `pass: false` не приходил ни разу, то есть это не «сломанный n-token», а
«сайт не отдаёт pass без интерактивного ответа». На Steam ключ ведёт с `custom_theme` +
`enc_get_req`; на `authenticate.riotgames.com` в features приходит `andint` — та самая anti-detect
VM, из-за которой jsdom-отпечаток заведомо не проходит (см. `D:/riot-research/hcaptcha-re-report.md`).

Проверка, что инструмент в принципе умеет pass: контрольный ключ hCaptcha отвечает
`checksiteconfig` как `{"pass": true}` (без `c`) и отдаёт pass UUID на пустом n-token — 10/10.
Значит 0% на Steam/Riot — свойство сайта, а не сломанный транспорт или бандл.

## Epic: hCaptcha на публичном входе нет

Проверено на живых страницах (`https://www.epicgames.com/id/login`, `/id/register`,
`https://accounts.epicgames.com/` — все три редиректят в один и тот же portal):

- в HTML стоит тег `<script src="https://js.hcaptcha.com/1/api.js?render=explicit&uj=true">` — он
  загружается;
- в 149 ленивых чанках portal'а (`static-assets-prod.unrealengine.com/account-portal/static/assets/`)
  **нет ни одного вхождения** `hcaptcha`; компонент `Captcha` переключается только по
  `case "talon"`, всё остальное рендерит пустой `div`;
- ключа нет ни в HTML, ни в чанках: `sitekey` не встречается ни разу;
- `store.epicgames.com/.../login` отдаёт 403 не-браузеру (Cloudflare), внутрь не смотрим.

Вердикт: ключ не снят, потому что его нет в публичных артефактах; enforcement на входе — Talon
(`talon-website-prod.ecosec.on.epicgames.com`, flow `login_prod`). Гадать sitekey нельзя, поэтому
в `solvers/hcaptcha/sites.py` epic остаётся `None` + `FILL`.

## Где Steam отдаёт ключ

Виджет рендерится из ответа XHR, в HTML ключа нет:

~~~python
r = client.post("https://store.steampowered.com/join/refreshcaptcha/",
                data={"count": "1", "hcaptcha": "1"},
                headers={"X-Requested-With": "XMLHttpRequest",
                         "Referer": "https://store.steampowered.com/join/"})
# {"gid": "...", "type": 3, "sitekey": "e18a349a-46c2-46a0-87a8-74be79345c92", "s": null}
~~~

`type: 3` — hCaptcha (1/2 — картинка/reCAPTCHA). Дальше `joinsteam.js` зовёт
`hcaptcha.render(..., {sitekey})` (`RenderHCaptcha`). Тот же ключ принимается на
`steamcommunity.com`: `checksiteconfig` вернул challenge, то есть привязка host на этом шаге не
проверяется — но `getcaptcha` всё равно требует challenge, поэтому строка помечена browser.

## Найдено по дороге (не правил — файлы не мои)

1. **`adapters/hcaptcha_js` ломает stdio-протокол.** `realm.js` создаёт jsdom без
   `virtualConsole`, поэтому `console.log` из бандла уходит в stdout воркера. Steam-бандл пишет
   строку `undefined` после solve, и следующий `_call` читает её как ответ:
   `JSRuntimeError: bundle worker returned non-JSON`. В прогоне — 20 посторонних строк на 20
   Steam-solve (Riot-бандл в этом прогоне не логировал). Для Steam это значит, что падает не
   второй task, а первый же: после solve в том же task'е идёт `encrypt` (`enc_get_req`), и он
   получает ту же строку. Проба обходит это, отбрасывая не-JSON строки и считая их
   (`summary.stray_lines`).
2. **Passive-ключи солвер считает нерешаемыми.** `{"pass": true}` без `c` — легальный ответ
   hCaptcha (тестовый ключ, пассивные деплойменты). `wire.checksite_config` бросает
   `UnsolvableError`, хотя такой сайт решается вообще без JS: `POST /getcaptcha/<sitekey>` формой
   с пустым `n` отдаёт pass UUID за ~150 мс. Это дешёвый +1 к покрытию HTTP-лейна.
3. `andint` в features на `authenticate.riotgames.com` подтверждает заметку из D:/riot-research:
   протокол байт-корректный, отпечаток не тот.

## Воспроизвести

~~~powershell
# зависимости бандла (один раз на чекаут; node_modules не вендорится)
cd src/captcha/adapters/hcaptcha_js; npm ci; cd ../../../..

# 10 прогонов по каждому известному сайту (50 всего), одной строкой
D:/captcha/.venv/Scripts/python.exe tools/hcaptcha_probe.py probe --runs 10 --interval 1.0 --node-path D:/riot-research/node_modules --out C:/Users/Public/hcaptcha-probe-run.json

# один сайт / свой ключ
D:/captcha/.venv/Scripts/python.exe tools/hcaptcha_probe.py probe --runs 5 --site account.riotgames.com=b3c619fc-4b72-4838-9bf2-398888588d62

# ключ со страницы (ищем api.js и литеральный sitekey в HTML и скриптах)
D:/captcha/.venv/Scripts/python.exe tools/hcaptcha_probe.py discover --url https://store.steampowered.com/join/ --url https://www.epicgames.com/id/login
~~~

Если Node-зависимостей нет, проба падает на старте воркера с
`bundle worker returned nothing: ... Cannot find module 'jsdom'` и подсказкой `npm ci`.

## Ограничения

- Один egress IP, без прокси и без смены отпечатка: числа — свойство этой точки, не «pass rate
  солвера вообще».
- `challenge` не различает «сайт всегда требует интерактив» и «n-token отвергнут по отпечатку».
  Для Riot это одно и то же по эффекту; для Steam — вопрос открыт, закрывается только прогоном
  через browser-лейн с реальной сессией.
- Контрольный ключ проверяет транспорт и протокол, но не отпечаток: 100% на нём не означает
  100% на живом сайте.
- `steam-community` — не отдельный деплой, а проверка того же ключа на другом host.
- Числа сняты без `vm_data` (бандл видит `null`); с коллектором отпечатка они могут измениться —
  это следующий эксперимент, не этот.
