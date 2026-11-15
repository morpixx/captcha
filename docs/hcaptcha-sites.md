# hCaptcha sites: live capture, HTTP pass rate, verdicts

Снимок 2026-11-15 03:10 Europe/Berlin, ветка `feat/research-sites` поверх мержа `feat/prod-base` (`9cc1d8f`: фикс realm и пассивная ветка уже в базе). Инструмент — `tools/hcaptcha_probe.py`, он же источник всех чисел ниже.

Задача файла — ответить на один вопрос по каждому хосту: **можно ли получить токен по HTTP**
(checksiteconfig -> n-token -> getcaptcha), или нужен браузер. Не «какой солвер лучше», а
«какой lane вообще имеет шанс».

## Метод

Один прогон = один task по контракту (`docs/production-contract.md` §2):

1. `POST /checksiteconfig?v=<build>&host=&sitekey=&sc=1&swa=1&spst=1` — новый HTTP-клиент
   (`curl_cffi`, impersonate `chrome131`), session-per-run;
2. n-token = hsw/hsj bundle на challenge JWT — через продакшен-раннер
   `adapters.hcaptcha_js.JsBundleRunner` (jsdom + canvas), без локальных обходов;
3. `POST /getcaptcha/<sitekey>` — форма, msgpack+Ext(18) при `enc_get_req`, или пассивная форма
   без n-token, если `checksiteconfig` ответил `{"pass": true}`.

Итог прогона: `pass` (P1_ / pass UUID), `challenge` (сервер вернул новый `c` вместо токена),
`reject` (pass=false и без challenge), `error` (транспорт/протокол/бандл).

## Границы метода: домен и страница

hCaptcha привязывает токен к sitekey-хосту **и к странице, которая его сминтила**; `n` — это
сериализованный отпечаток, а не переносимый шифротекст. Проба минтует `n` в jsdom с
`href=https://<host>/` и без `vm_data` со страницы, поэтому `challenge` на защищённых ключах
(Steam/Riot) — ожидаемый результат, а не «pass rate сайта». Для этих хостов production-путь —
browser-лейн **на том же origin**: `D:/riot-research/riot_hcaptcha_minter.py` открывает
`account.riotgames.com`, инжектит `api.js` в эту страницу и получает `P1_` с `host=account...`;
токен нельзя минтить на одном домене и отправлять на другом. Контроль `example.com` — passive
ключ, он не проверяет перенос токена между доменами.

Команда:

~~~powershell
D:/captcha/.venv/Scripts/python.exe tools/hcaptcha_probe.py probe --runs 10 --interval 1.0 --node-path D:/riot-research/node_modules --out C:/Users/Public/hcaptcha-probe-run2.json
~~~

50 прогонов (5 сайтов x 10), последовательно, один egress IP, без прокси. `client_version` —
`wire.DEFAULT_VERSION`, UA — `wire.DEFAULT_UA` (Chrome 131 / Win64). Node 26.13.0, jsdom 29.1.1,
canvas 3.2.3 (взяты из `D:/riot-research/node_modules`, read-only; `npm ci` в адаптере не
запускался — см. «Воспроизвести»).

## Матрица

| host | sitekey | где живёт | HTTP прогонов | pass | challenge | pass rate | p50 / p95, ms | вердикт |
|---|---|---|---|---|---|---|---|---|
| `store.steampowered.com` | `e18a349a-46c2-46a0-87a8-74be79345c92` | `/join/`, XHR `/join/refreshcaptcha/` | 10 | 0 | 10 | 0% | 428 / 8467 | browser |
| `steamcommunity.com` | `e18a349a-46c2-46a0-87a8-74be79345c92` | тот же ключ, виджета на `/login/home/` нет | 10 | 0 | 10 | 0% | 701 / 2623 | browser |
| `account.riotgames.com` | `b3c619fc-4b72-4838-9bf2-398888588d62` | Riot OAuth/account (D:/riot-research) | 10 | 0 | 10 | 0% | 531 / 4714 | browser |
| `authenticate.riotgames.com` | `a5f74b19-9e45-40e0-b45d-47ff91b7a6c2` | Riot authenticate, feature `andint` | 10 | 0 | 10 | 0% | 340 / 2312 | browser |
| `example.com` (контроль) | `10000000-ffff-ffff-ffff-000000000001` | тестовый ключ hCaptcha (passive) | 10 | 10 | 0 | 100% | 582 / 1223 | http |
| `www.epicgames.com` | — | hCaptcha не найдена, виджет — Talon | — | — | — | — | — | вне матрицы |

Вердикты: **http** — HTTP-лейн выдаёт токен; **browser** — HTTP доходит до challenge и токена не
получает, нужен браузер (и, если сайт показывает картинку, ответ на неё); **auto** — данных нет,
профиль остаётся `SolveMode.AUTO` и решает browser-лейн.

Стадии (p50, ms), те же прогоны:

| site | checksiteconfig | n-token | getcaptcha | длина n-token |
|---|---|---|---|---|
| steam-join | 132 | 111 | 160 | 39 004 … 40 064 (медиана 39 724) |
| steam-community | 224 | 109 | 153 | 39 068 … 40 092 |
| riot-account | 347 | 9 | 111 | 14 092 … 14 156 |
| riot-authenticate | 108 | 10 | 97 | 14 100 (без разброса) |
| hcaptcha-test-key | 536 | — (не нужен) | 51 | — |

`p95` шумный: одиночные выбросы (Steam #1 — 8.5 с на прогрев realm, Riot #1 — 4.7 с),
поэтому ориентир — `p50` и среднее (`total_ms_mean` 0.7-1.7 с). После прогрева типичный прогон
0.3-1.2 с.

## Что значит 0/10

`challenge` — сервер принял запрос и вернул **новый** `c` (`type: hsw`, `req`) вместо pass-токена.
Ответ с `error-codes` и `pass: false` не приходил ни разу, то есть это не «сломанный n-token», а
«сайт не отдаёт pass без интерактивного ответа». На Steam ключ ведёт с `custom_theme` +
`enc_get_req`; на `authenticate.riotgames.com` в features приходит `andint` — та самая anti-detect
VM, из-за которой jsdom-отпечаток заведомо не проходит (см. `D:/riot-research/hcaptcha-re-report.md`).

Проверка, что инструмент в принципе умеет pass: контрольный ключ hCaptcha отвечает
`checksiteconfig` как `{"pass": true}` (без `c`) и отдаёт pass UUID на пустом n-token — 10/10,
сам `getcaptcha` ~50 мс. Значит 0% на Steam/Riot — свойство сайта, а не сломанный транспорт или
бандл.

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

## Что дал мерж feat/prod-base

Прошлый снимок (02:20, до мержа) снимался на локальном клиенте воркера: продакшен-раннер падал.
jsdom без `VirtualConsole` выпускал `console.log` бандла в stdout, и следующий `_call` читал
`undefined` как ответ (`JSRuntimeError: bundle worker returned non-JSON`). Для Steam это ломало
первый же task — после solve в нём идёт `encrypt` (`enc_get_req`).

После мержа:

- в `realm.js` появился `VirtualConsole`, вывод бандла больше не попадает в протокол;
- в `wire.py`/`http.py` появилась пассивная ветка (`CheckConfig`, `get_captcha_passive`,
  `pass_token` принимает bare UUID);
- проба переведена на продакшен-раннер и `wire` без обходов: 50 прогонов, **0 ошибок протокола**,
  ни одной посторонней строки (до мержа — 20 на 20 Steam-solve);
- `p50` поэтому ниже прежнего (428/701/531/340 против 798/910/761/994): ушёл оверхед локального
  клиента и холодного старта на каждый прогон. Поведение сайтов не изменилось — те же 0/10
  `challenge`.

## Воспроизвести

~~~powershell
# зависимости бандла (один раз на чекаут; node_modules не вендорится)
cd src/captcha/adapters/hcaptcha_js; npm ci; cd ../../../..

# 10 прогонов по каждому известному сайту (50 всего), одной строкой
D:/captcha/.venv/Scripts/python.exe tools/hcaptcha_probe.py probe --runs 10 --interval 1.0 --node-path D:/riot-research/node_modules --out C:/Users/Public/hcaptcha-probe-run2.json

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
- `p95` на 10 прогонах нестабилен (сетевые выбросы); для решений смотреть `p50`/`mean`.
