# Runbook: geetest_v4

## 1. Snapshot

- **captcha type:** 'CaptchaType.GEETEST_V4' — тот же слайдер, другой набор полей и собственный proof-of-work
- **antibot family:** GeeTest — слайдер четвёртого поколения
- **mode:** 'http' (CV-примитив вызывается in-process через 'InteractiveBase._solve_cv')
- **tier:** 3 (skeleton→protocol) — публичная схема 2026 портирована, live-сессия не снималась
- **lane:** 'cv'
- **experimental:** 'True' до L3 canary

## 2. Payload shape

~~~json
{
  "type": "geetest_v4",
  "websiteURL": "https://target.example/login",
  "captcha_id": "<captcha_id>",
  "risk_type": "slide",
  "challenge": "<optional uuid; generated when absent>",
  "payload": "<optional, load response wins>",
  "process_token": "<optional, load response wins>",
  "pt": "<optional, load response wins>"
}
~~~

Обязательные: 'websiteURL', 'captcha_id' (принимается и 'captchaId'). 'risk_type'
может быть 'slide' или 'slide|<signed suffix>' — значение копируется в '/load' и
'/verify' дословно. 'ai'/'invisible'/'icon' не решаются CV-слайдером → 'UnsolvableError'.

## 3. Flow

1. 'GET /load?captcha_id=&challenge=<uuid4>&client_type=web&risk_type=&lang=&callback=' →
   'lot_number', 'pow_detail', 'bg'/'slice', 'pt', 'payload', 'process_token'.
2. Скачать обе картинки, отдать в '_solve_cv(SLIDER, …)' → float offset.
3. PoW: 'solve_pow' по 'pow_detail{version,bits,hashfunc,datetime}'.
4. 'w' по 'pt': '0' — urlencoded raw JSON; '1' — AES-128-CBC + RSA; '2' — не реализовано.
5. 'GET /verify?callback=&captcha_id=&client_type=&lot_number=&risk_type=&payload=&process_token=&payload_protocol=1&pt=&w=' →
   'seccode'/'captcha_output'/'pass_token'/'gen_time'.

## 4. Тело 'w' (2026)

'lot_number' даёт вложенный ключ из трёх групп срезов (границы включительно):
'((28,29),(17,18))' → '((5,10))' → '((0,0),(15,15),(25,25),(30,30))', значение '((22,27))'.
Остальное: 'bzj5=MoPY', 'pow_msg', 'pow_sign', 'biht', 'device_id=""', 'em{...}',
'gee_guard.roe{...}', 'ep=123', 'geetest=captcha', 'lang=zh', 'lot_number', 'passtime'
600–1200, 'setLeft' = offset, 'userresponse' = 'left / 1.0059466666666665 + 2'.

## 5. FILL: что ещё снять с живой сессии

| # | где | что нужно |
|---|---|---|
| 1 | 'payload.encrypt_w' | схема 'pt=2' ('createPoint/initEncipher/encryptBlock') — публично не реализована |
| 2 | 'payload.py' v4-константы | сверить 'biht','ep','em','gee_guard','bzj5' по одному расшифрованному 'w' |
| 3 | 'endpoints.V4_DEFAULT_API' | какой хост у целевого сайта: 'gcaptcha4.geetest.com' или 'gcaptcha4.geevisit.com' |
| 4 | 'payload.V4_LOT_KEY_GROUPS' | перевывести срезы, если 'gcaptcha4.js' обновится |

## 6. Ошибки

- 'ChallengeExpiredError' — '/load' вернул 'status != success' или пустой 'lot_number'.
- 'UnsolvableError' — нет 'captcha_id', нет картинок, 'pt=2', non-slide 'risk_type'.
- 'ConfigError' — невалидный 'api_server', пустой RSA-модуль.
- 'CaptchaError' — '/verify' без 'captcha_output'/'seccode' или 'status=fail' без expiry-хинта.

## 7. Solution keys

'captcha_id', 'lot_number', 'pass_token', 'gen_time', 'captcha_output' — проекция в
'domain/solution.py'. Новые сборки отдают только 'seccode'; солвер подставляет его
в 'captcha_output' и 'pass_token'.

## 8. Проверка

~~~bash
uv run pytest -q tests/solvers/interactive/test_geetest.py tests/solvers/interactive/test_geetest_flow.py
uv run ruff check src/captcha/solvers/interactive/geetest tests/solvers/interactive
~~~

## 9. Известные риски

- Набор полей 'w' меняется чаще, чем в v3: при поломке первым делом сверять тело.
- 'risk_type' с подписью time-bound; клиент обязан передать его дословно и свежим.
