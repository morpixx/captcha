# Live browser probes, 2026-11-15

Кампания: проверить, что реально даёт browser lane на живых сайтах и можно ли
получить L3 на публичных тестовых ключах. Camoufox 0.5.6 установлен в
`D:/captcha/.venv`; браузерный кэш — `C:/Users/Roman/AppData/Local/camoufox`.
Каждый прогон — свежий профиль, headless, без прокси.

## hCaptcha (tools/hcaptcha_browser_probe.py)

| target | URL | результат | латентность | контекст |
|---|---|---|---|---|
| riot | https://account.riotgames.com/ | `ERR_UNSOLVABLE` | 5.9 s | редирект на `authenticate.riotgames.com`; guard корректно отверг смену origin |
| riot-auth | https://authenticate.riotgames.com/ | `ERR_CHALLENGE_EXPIRED` | 95.6 s | `challenge_shown`, host `authenticate.riotgames.com` |
| steam | https://store.steampowered.com/join/ | `ERR_CHALLENGE_EXPIRED` | 95.2 s | `challenge_shown`, host `store.steampowered.com` |
| control | https://example.com/ | `ERR_UNSOLVABLE` | 5.3 s | токен `1000…` длиной 36; тестовый ключ отдаёт placeholder, solver его отвергает |

Вывод: Riot и Steam действительно доходят до hCaptcha в реальном браузере, но
сайты показывают image-challenge. Без солвера картинки (и без реального
аккаунта/прокси) токена нет. Тестовый ключ `10000000-ffff…` даёт не P1_, а
placeholder; он годится только для smoke, не для L3.

## Turnstile (tools/turnstile_probe.py)

`--scenario visible-pass` на loopback с ключом `1x00000000000000000000AA`:

- виджет отрисовался и вернул токен за ~5.8 s;
- токен: `XXXX.DUMMY.TOKEN.XXXX` (21 символ);
- `TurnstileSolver` корректно отверг его как `unexpected token shape`
  (`ERR_UNSOLVABLE`), потому что это документированный Cloudflare dummy-токен.

Вывод: тестовые ключи Cloudflare проверяют только browser-flow (виджет, API,
событие), но не дают пригодного токена. L3 Turnstile требует реальный sitekey и
страницу.

## Что это значит для 62/62 L3

1. Публичные тестовые ключи hCaptcha/Turnstile не дают валидного токена —
   нужны реальные sitekey/аккаунты/прокси.
2. Riot/Steam требуют решения image-challenge; browser-лейн сам по себе его не
   решает.
3. 7-дневная canary и пороги не могут быть закрыты на тестовых ключах.

Инструменты не отправляют формы, не меняют аккаунты и не пишут в
`D:/project`, `D:/riot-research`, `D:/hopscotch_v2`.
