# Runbook: geetest_v3

## 1. Snapshot

- **captcha type:** 'CaptchaType.GEETEST_V3' — слайдер с разрезом картинки и AES-обёрнутым payload
- **antibot family:** GeeTest — слайдер третьего поколения
- **mode:** 'http' (CV-примитив вызывается in-process через 'InteractiveBase._solve_cv')
- **tier:** 3 (skeleton→protocol) — публичная схема 2026 портирована, live-сессия не снималась
- **lane:** 'cv'
- **experimental:** 'True' до L3 canary

## 2. Payload shape

~~~json
{
  "type": "geetest_v3",
  "websiteURL": "https://target.example/login",
  "gt": "<gt>",
  "challenge": "<challenge>",
  "api_server": "<optional host>",
  "risk_type": "<optional, e.g. slide>"
}
~~~

Обязательные: 'websiteURL', 'gt', 'challenge'. 'api_server' — переопределение хоста
(легаси-клиенты шлют его как 'api_server'); enterprise-сайты route'ят GeeTest через
свой поддомен и передают его здесь. 'risk_type' прокидывается в 'get.php'/'ajax.php',
если сайт его требует.

## 3. Flow

1. **register** — сайт получает 'gt' + 'challenge' у себя; в задаче они уже есть.
2. 'GET /get.php' — round metadata + первый 'w' (custom AES-хвост + RSA).
3. 'GET /ajax.php?type=slide' — режим раунда; 'bg'/'slice' тут редко, но остаются fallback'ом.
4. 'GET /get.php' #2 — перемешанные 'bg'/'fullbg'/'slice', 'c'/'s' и 'challenge' + 2 символа (34).
5. 'POST /ajax.php' — answer 'w' (только AES-половина) → 'validate'/'seccode'.

## 4. Wire-схема (2026)

- 'w' = 'custom_encode(AES-128-CBC(body, key16, iv="0000000000000000"))' + 'hex(RSA-PKCS1v15(key16))'
  на первом раунде; на answer-раунде RSA-хвоста нет (два публичных разбора 2025 совпадают).
- 'custom_encode' — модифицированный base64 ('p[$_CEEJe(1196)]'): 3 байта → 4 символа
  через маски '7274496/9483264/19220/235', алфавит 'A-Za-z0-9()', хвост добивается '.'.
- 'userresponse' = 'H(offset, challenge34)': последние 2 base36-символа дают сдвиг,
  первые 32 дедуплицируются в 5 корзин, вес'а '1/2/5/10/50' разбирают сумму.
- 'aa' = 'getAA(track_encoded, c, s)': байты 's' вставляются в зашифрованный трек
  по '(c0*x^2 + c2*x + c4) % len(track)'.
- 'rp' = 'md5(gt + challenge[:32] + passtime)' на первом раунде и
  'md5(gt + challenge + last track t)' на answer-раунде.
- RSA-модуль и 'e=65537' — публичный ключ GeeTest (тот же, что в v4).

## 5. FILL: что ещё снять с живой сессии

| # | где | что нужно |
|---|---|---|
| 1 | 'v3_payload.V3_SDK_VERSION' | 'ep.v' из одного расшифрованного verify-тела |
| 2 | 'wire.V3_ENCODER_ALPHABET' | подтвердить алфавит/маски в текущем 'slide.<ver>.js' |
| 3 | 'v3.py' перед '_solve_cv' | порядок восстановления 52 тайлов 'bg' (сейчас CV видит перемешанную пару) |
| 4 | 'v3.py' 'aa' | подтвердить, что 'c'/'s' всегда приходят во втором 'get.php' |
| 5 | 'v3.py' первый 'w' | проверить, что сервер принимает непустой первый 'w' с пустым 'userresponse' |

## 6. Ошибки

- 'ChallengeExpiredError' — 'get.php'/'ajax.php' вернули 'status != success' (stale challenge).
- 'UnsolvableError' — нет 'gt'/'challenge', нет картинок, 'challenge' короче 34, неизвестный hash в PoW.
- 'ConfigError' — невалидный 'api_server', пустой RSA-модуль.
- 'CaptchaError' — verify без 'validate'.

## 7. Solution keys

См. 'domain/solution.py' — 'challenge', 'validate', 'seccode'. Vendor-специфичные
поля уходят в 'SolverResult.extra' насквозь.

## 8. Проверка

~~~bash
uv run pytest -q tests/solvers/interactive/test_geetest.py tests/solvers/interactive/test_geetest_flow.py
uv run ruff check src/captcha/solvers/interactive/geetest tests/solvers/interactive
~~~

## 9. Известные риски

- Модуль/энкодер меняются с релизом бандла — это первый источник «перестало работать».
- 'userresponse' не равен сырому offset: неверный H даёт 'fail' при визуально верном ответе.
- Перемешанный 'bg' без восстановления тайлов даёт смещённый offset.
