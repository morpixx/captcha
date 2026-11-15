# Реверс-карта 2026: что публично известно о wire-форматах

Состояние на **2026-11-15**. Цель — заменить `# FILL:` из `runbooks/{geetest_v3,geetest_v4,capy,keycaptcha,lemin}.md`
и `src/captcha/{solvers/interactive,engines}/` фактами. **[проверено]** — страница скачана и прочитана
в этой сессии; **[не проверено]** — только сниппет выдачи.

Вердикты: **HTTP-only** — всё снимается HTTP-клиентом, крипта портируется в Python; **js-runtime** — нужен
Node/V8 и запуск чужого бандла, GUI не нужен; **браузер** — нужен реальный Chrome, гейт серверный.
## 1. GeeTest v3
**Wire.** Пять запросов: `register` (gt+challenge) → `get.php` (w1) → `ajax.php` (w2) → `get.php` #2
(перемешанная картинка + **новый 34-символьный challenge**) → `ajax.php` #2 (w3). `w = i + r`:
`r` = RSA-PKCS1v15 от 16-символьного случайного ключа (`setPublic(N,e)`, `e=65537`); `i` = AES-128-CBC
того же ключа, IV = 16 ASCII `'0'`. AES-выход **не отдаётся как hex**: байты → «числовой массив + длина» →
кастомный энкодер (`p[$_CEEJe(1196)](o)`); это и есть строка в `w`. `userresponse = H(offset, challenge34)` —
хеш, не сырой offset. `aa` = шифр трека с `c`/`s` из ответа **второго** `get.php`. `rp` w2 = md5(gt+challenge[:32]+passtime),
w3 = md5(gt+challenge+последний t трека). У **второго** `w` RSA-хвоста нет — только AES-половина (два разбора
2025 года независимо). Бандлы версионируются по типу (`slide.<ver>.js`, `click.<ver>.js`; в 2023 — `slide.7.8.9.js`),
единой «версии SDK» нет: `V3_SDK_VERSION = "9.0.7"` ничем не подтверждён.

Источники: [cnblogs 2025-12-29](https://www.cnblogs.com/ddkt/p/19413810) [проверено] — IV, setPublic/65537, H(), aa, rp, второй w без RSA ·
[jishuzhan 2025-11-29](https://jishuzhan.net/article/1994658950592135170) [проверено] — 5 запросов, 34-символьный challenge, кастомный энкодер ·
[52pojie 2025](https://www.52pojie.cn/thread-2063429-1-1.html) [не проверено] — та же схема ·
[segmentfault 2023-03](https://segmentfault.com/a/1190000043605773) [не проверено] — **устарело**, версии бандлов 7.8.9/3.0.7.

**Съёмка:** расшифрованные тела w1/w2/w3 одной сессии, тела `H` и `getAA`, `setPublic`-модуль текущего
`slide.<ver>.js`, диапазон `imgload`, наличие `h9s9`.

**Вердикт: HTTP-only**, но лишь после порта трёх чистых функций (энкодер i, H, getAA). До этого — фактически
**js-runtime** на один прогон, чтобы их вытащить.
## 2. GeeTest v4
**Wire.** `GET /load` (`captcha_id`, `challenge`=uuid4, `client_type=web`, `risk_type`, `lang`, `callback`) →
`lot_number`, `pow_detail{version,bits,hashfunc,datetime}`, `bg`, `slice`, `pt`, `payload`, `process_token`.
`pt` выбирает обёртку: **0** — `w` = urlencode сырого JSON; **1** — `w = hex(AES-128-CBC(json, uid16, iv="0000000000000000")) + hex(RSA-PKCS1v15(uid16))`
с модулем **`00C1E3934D16…AB81`** (1024 бита), `e=0x10001`; **2** — другая схема (`createPoint/initEncipher/encryptBlock`,
ключ `9a4ea935b2576f37…`), публично не реализована. Тело: `lot_number, pow_msg, pow_sign, biht:"1426265548",
ep:"123", geetest:"captcha", lang:"zh", em{cp,ek,nt,ph,sc,si,wd}, gee_guard.roe{…8 ключей…}, bzj5:"MoPY",
device_id:"", passtime(600–1200), setLeft, userresponse` + ключ из срезов `lot_number`: три группы через `.`
(`(n[28:29]+n[17:18])`, `(n[5:10])`, `(n[0:0]+n[15:15]+n[25:25]+n[30:30])`), значение `n[22:27]`.
Наш `V4_LOT_KEY_GROUPS` содержит **две** группы из трёх — расхождение. `userresponse` слайдера =
`left / 1.0059466666666665 + 2`. `/verify` — **GET** с query `callback, captcha_id, client_type, lot_number,
risk_type, payload, process_token, payload_protocol=1, pt, w`; хост сборки 2026-10 — `gcaptcha4.geevisit.com`,
в документации вендора — `gcaptcha4.geetest.com`. Версия SDK: **v1.9.9-53b07e** (2026-10-30).

Источники: [xKiian/GeekedTest](https://github.com/xKiian/GeekedTest) [проверено] — README, `geeked/sign.py`, `geeked/geeked.py`;
pure-python, без браузера ·
[коммиты](https://api.github.com/repos/xKiian/GeekedTest/commits) [проверено] — 2026-10-30, v1.9.9-53b07e ·
[52pojie 2026-09](https://www.52pojie.cn/forum.php?mod=viewthread&tid=2128465) [не проверено] — w=AES+RSA, PoW + gzip/base64url `td` ·
[CSDN 2025-12](https://blog.csdn.net/weixin_30591519/article/details/156308525) [не проверено] — та же формула w.

**Съёмка:** значение `pt` на целевом сайте, актуальность модуля, три группы срезов, семантика `payload`/`process_token`
(их нет в нашем солвере), GET vs POST у `/verify`, набор ключей ответа.

**Вердикт: HTTP-only.** Рабочая публичная pure-python имплементация 2026 года это закрывает.
## 3. Cloudflare Turnstile
**Wire (живая съёмка 2026-08).** `GET challenges.cloudflare.com/turnstile/v0/api.js` (302) → версионированный
бандл `/turnstile/v0/b/<hash>/api.js` → `POST /cdn-cgi/challenge-platform/h/b/turnstile/f/av0/rch/<seg>/<sitekey>`
→ токен в `cf-turnstile-response`. Виджет читает `sitekey`, `action`, `cData`, `chlPageData` (последний
инжектится в рантайме). Токен одноразовый, TTL ~300 с, привязан к hostname, проверяется на
`/turnstile/v0/siteverify` секретом, который не покидает сервер. **Байтовой схемы payload шага 3 в открытом
доступе нет** — автор единственного публичного тиража помечает её как «gated, not filled from memory».
Все найденные открытые имплементации водят реальный браузер.

Источники: [cloudflare-turnstile-reversed, 01-challenge-flow.md](https://github.com/CircuitSavage/cloudflare-turnstile-reversed/blob/main/docs/01-challenge-flow.md) [проверено] ·
[02-widget-params.md](https://github.com/CircuitSavage/cloudflare-turnstile-reversed/blob/main/docs/02-widget-params.md) [проверено] ·
[PyPasser turnstile.md](https://github.com/xhossein/pypasser/blob/main/docs/solvers/turnstile.md) [проверено] — Patchright/Camoufox, headless часто блокируется ·
[JWally/dfp-redteam-index 2026-11-01](https://github.com/JWally/dfp-redteam-index) [проверено] — «client is a shuttle, proof is server-issued» ·
[art3m4ik3/cloudflare-solver](https://github.com/art3m4ik3/cloudflare-solver) [проверено] — только браузер.

**Съёмка:** тело шага 3 (поля, PoW-параметры), реальный TTL токена, есть ли путь без `cdata`.
**Вердикт: браузер.** Форж токена не описан нигде.
## 4. Cloudflare managed challenge (cf_clearance)
**Wire (2026-10).** Триггер — заголовок ответа `cf-mitigated: challenge`. Headful Chrome (nodriver;
headless флагится) проходит 5-секундную проверку, получает `cf_clearance`; кука валидна только после
исчезновения самой страницы проверки — выданная до финального редиректа отклоняется. `cf_clearance`
привязана к **UA и IP**: реплей с другим UA/прокси даёт `ChallengeError`. Интерактивный Turnstile внутри:
CDP-клики мышью (bezier/jitter/pressure) отвергаются все, проходит `Tab`+`Space`. Кэшированный реплей
0.02–0.1 с против 2.9–4.3 с на браузерное решение.

Источники: [tn3w/cloudflare-skip (PyPI) 2026-10-03](https://pypi.org/pypi/cloudflare-skip/json) [проверено] — `cf-mitigated`, привязка к UA/IP, Tab+Space, тайминги ·
[blog.peak.fo, __cf_chl_tk](https://blog.peak.fo/cf-chl-tk-explained/) [не проверено] — сам `__cf_chl_tk` ничего не даёт.

**Съёмка:** набор полей challenge-страницы, зависит ли TTL от сайта, проходит ли headless с патченым TLS на чистом IP.
**Вердикт: браузер.** Портировать нечего — доказательство выдаёт сервер.
## 5. DataDome
**Wire (2026-06 … 2026-11).** `tags.js`, ключ `window.ddjskey`, POST на `https://api-js.datadome.co/js/`.
`jspl` — **не крипта, а обратимый xorshift-XOR** (`n^=n<<13; n^=n>>17; n^=n<<5`), сид = хеш от **публичного**
`ddjskey` + константа `11027890091` + таймстемп запроса, поверх кастомный base64 `-_0-9A-Za-z`; HMAC/AEAD
нет. Инвентарь v5.6.6 (расшифрован): **203 сигнала** — мышь, клавиатурная динамика (`k_hA/k_hSD`), `es_*`,
сетевые тайминги `nt_*`, canvas/CSS (`css*`, `fph`), self-stack-integrity (`ccs*`), UA-CH, WebGL, детекторы
AI-агентов (`gai`, `sqt`). Бандл **v5.11.2** константу сохранил, но генератор переписан (декодер v5.6.6 даёт
41% printable) — keystream надо выводить заново. Кука `datadome` и Device Check **выдаются сервером**.
На живом деплое tags-эндпоинт принимает и нетронутый, и испорченный пейлоад (перевёрнутый байт `jspl`,
фейковый `cid`) и отдаёт 200 + свежую куку. Капча-ветки: interstitial POST на `https://geo.captcha-delivery.com/interstitial/`,
slider — GET по check-URL, скрипт челленджа отдельным файлом `https://ct.captcha-delivery.com/interstitial.1.33.0.202609141.js`.

Источники: [dfp-redteam-index (DataDome) 2026-11-01](https://github.com/JWally/dfp-redteam-index) [проверено] — xorshift, сид, 203 сигнала, v5.6.6 vs v5.11.2 ·
[hyper-sdk-py README 2026-11-07](https://github.com/hyper-solutions/hyper-sdk-py/blob/4ecb75c6a5d5aee6c00d760a538eb3c00bbc079e/README.md) [проверено] — endpoint'ы, `interstitial.1.33.0.202609141.js` ·
[datadome-encryption ENCRYPTION.md](https://github.com/glizzykingdreko/datadome-encryption/blob/main/docs/ENCRYPTION.md) [не проверено] — PRNG-XOR + кастомный base64 ·
[DataDome docs 2026-08-20](https://docs.datadome.co/docs/how-to-configure-the-javascript-tag) [не проверено] — дефолтный endpoint ·
[ellisfan/bypass-datadome 2024](https://github.com/ellisfan/bypass-datadome) [не проверено] — **устарело**.

**Съёмка:** актуальный keystream v5.11.x, имена полей POST (`jspl, cid, ddk, hsh, responsePage, ua` — ровно то,
что в нашем билдере), формат interstitial/slider-пейлоада.

**Вердикт:** tags — **HTTP-only**; кука через Device Check/interstitial — **js-runtime**; полная сессия — **браузер**.
## 6. Kasada
**Wire (2026-05 … 2026-11).** 429 отдаёт HTML с путём скрипта (`/ips.js?timestamp=…`), пин версии в `x-kpsdk-v`
(вида `j-1.1.0`). Заголовки: `x-kpsdk-ct` (сессионный, ~30 мин), `x-kpsdk-cd` (per-request PoW, одноразовый),
`x-kpsdk-st` (серверный таймстемп, к нему привязан cd), `x-kpsdk-h` (связывает ct и cd). `cd` — JSON с полями
вида `workTime, id, answers, duration, st`. Логика в байткоде собственной VM (интерпретатор + ~60 хендлеров,
байткод base62 → массив интов, строки отдельной секцией); PoW — sha256, допуск «несколько секунд».

Публичные имплементации 2026 расходятся: [B00H0O/kasada-vm 2026-06](https://github.com/B00H0O/kasada-vm) [проверено] —
гонит `p.js` VM в **Node-песочнице**, минтает `ct` из POST на `/tl`, `h` из `/mfc`, `cd` — WIP ·
[hyper-sdk-py](https://github.com/hyper-solutions/hyper-sdk-py/blob/4ecb75c6a5d5aee6c00d760a538eb3c00bbc079e/README.md) [проверено] —
их API принимает `script`/контекст и отдаёт `ct` для POST на `/tl` ·
[dfp-redteam-index (Kasada) 2026-11-01](https://github.com/JWally/dfp-redteam-index) [проверено] — на деплое Twitch
`/tl`-POST **нет вообще**: `ct` приходит в заголовке ответа `ips.js`; мусорный/пустой `cd` принимается
(проверяется наличие), VM сервером не верифицируется; сессия сминчена на 3 мерчантах.

**Источники расходятся** в двух пунктах: нужен ли POST на `/tl` (зависит от деплоя) и проверяется ли `cd`
(на Twitch — нет, в общем описании — да, одноразовый + окно свежести). [blog.crawlex.net 2026-05-20](https://blog.crawlex.net/blog/kasada-kpsdk-token-vm/) [проверено] — полный разбор VM/CT/CD/PoW.

**Съёмка:** есть ли `/tl`, откуда `ct` (тело или заголовок), обязателен ли `cd`, текущая `x-kpsdk-v`, привязка к IP/TLS.
**Вердикт: js-runtime.** Node + `p.js` достаточно; браузер не обязателен, но IP/TLS-привязка делает реплей хрупким.
## 7. PerimeterX (HUMAN Security)
**Wire (2026-05, валидировано на iFood и Grubhub).** *Sensorless* (99% сессий): два POST на
`https://collector-<app>.px-cloud.net/api/v2/collector`, form-urlencoded `appId, tag, ft, seq, en, uuid, vid, cts, sid, pc, payload`.
`en` = base64 от `"50"` (объявление XOR-ключа). `payload` = JSON → XOR("50") → XOR("10") → base64 (**latin1**,
не utf8) → перестановка соседних символов → base64 → urlencode. `pc` = HMAC-MD5 с солью `uuid:tag:ft`, из hex
берутся цифры, затем `a…f`→`7,8,9,0,1,2`, чётные индексы, обрезка до 10 (iFood) / 11 (Grubhub). `sid` несёт
невидимые variation selectors (U+E0000–U+E007F). Ответ `{"ob": "<base64>"}`: base64 → XOR → split по `~~~~` →
сегменты `name|arg|…`, имена матчатся **по форме**, 27 форм. *Bundle/press*: `/assets/js/bundle`, другие
константы (`tag=O2MKZn0OEhI/ag==`, `ft=388`, appId из OB#1), 4 POST + WASM + PoW.
Независимый тираж 2026-11 даёт **другую крипту транспорта**: одиночный XOR `0x32`, без MAC, base91-таблица,
75/189 имён сигналов — и вывод, что решает не пейлоад, а чистота IP/TLS/JA4 + честный payload на реальном Chrome.

Источники: [waterbili/PerimeterX_RE 2026-05-20](https://github.com/waterbili/PerimeterX_RE/blob/main/main/EN/PX_SDK_Reverse_Engineering.md) [проверено] — поля, XOR/interleave, PC, `sid`, OB-формы ·
[dfp-redteam-index (PX) 2026-11-01](https://github.com/JWally/dfp-redteam-index) [проверено] — XOR 0x32, base91 ·
[tuberboy/perimeterx 2025-03](https://github.com/tuberboy/perimeterx) [не проверено] — декодер/энкодер.
**Источники расходятся:** двойной XOR 50/10 + interleave против одиночного 0x32 — вероятно разные слои
(payload против транспортной обёртки), разводить живой съёмкой.

**Съёмка:** какой слой XOR на конкретном сайте, полный список полей EV2 (204+), `ft`/`tag` текущего билда, нужен ли press.
**Вердикт:** sensorless — **HTTP-only** после порта сериализатора и PC; bundle/press — **js-runtime** (WASM).
## 8. Imperva (Incapsula, reese84)
**Wire.** Публично известны только внешние факты: сенсор `reese84` + кука `___utmvc`, динамический путь
скрипта из interruption-страницы. Коммерческие API принимают **сам скрипт** (или его URL) на вход — внутри
они его исполняют. Побайтовой схемы пейлоада в открытом доступе нет; открытых HTTP-only имплементаций нет.

Источники: [hyper-sdk-py 2026-11-07](https://github.com/hyper-solutions/hyper-sdk-py/blob/4ecb75c6a5d5aee6c00d760a538eb3c00bbc079e/README.md) [проверено] — `generate_reese84_sensor`, `generate_utmvc_cookie`, `parse_dynamic_reese_script` ·
[Capsolver Imperva 2026-10-25](https://docs.capsolver.com/de/guide/antibots/imperva/) [не проверено] — `reeseScriptURL` обязателен ·
[rozetyp/rq4 SPEC 2026-03](https://github.com/rozetyp/rq4/blob/main/SPEC.md) [не проверено] — перенос `reese84` между сессиями проходит.

**Съёмка:** имя и структура поля-пейлоада, обязательные заголовки, что подписывается, живёт ли кука дольше сессии.
**Вердикт: js-runtime** (исполнить reese-скрипт). Браузер не обязателен, но без скрипта делать нечего.
## 9. hCaptcha (в т.ч. enterprise)
**Wire (2026-05 … 2026-06).** `js.hcaptcha.com/1/api.js` — загрузчик; машинерия на `newassets.hcaptcha.com`
(`hsj.js` asm.js-подобный, `hsw.js` Rust→WASM). Поток: запрос челленджа (sitekey, host, зашифрованные `n` и `c`,
телеметрия движения) → `hsw` PoW-штамп → passcode в `h-captcha-response` → `api.hcaptcha.com/siteverify`
(form-urlencoded, не JSON). Ключи **build-static**: пять AES-256-GCM (3 в hsj, 2 в hsw) извлекаются из живого
бандла за ~22 с; шестой (`hsw.n_key`) снимается с места вызова AES, но keystream не воспроизводит. Шифр
n-токена решён: **AES-256-CTR**, counter-блок `iv(12)‖be32(counter)`, формат `ct(N)‖tag(16)‖iv(12)‖ver(0x02)`.
Внешнее дешифрование чужого n-токена по-прежнему требует пер-билдового ключа.
**Источники расходятся:** тираж 2026-11 говорит, что `hsw` не инициализируется под автоматизацией и гейт
серверный; [52pojie 2025-11](https://www.52pojie.cn/thread-2074942-1-6.html) [не проверено] — в Node с захардкоженными
`vm_data`/`motionData` проходит. Оба варианта значат «нужен рантайм», вопрос лишь в GUI.

Источники: [hcaptcha-hsj-hsw-reversed 2026-06-03](https://github.com/CircuitSavage/hcaptcha-hsj-hsw-reversed) [проверено] — 6 ключей, GCM, CTR, пер-билдовое извлечение ·
[blog.crawlex.net hCaptcha 2026-05-08](https://blog.crawlex.net/blog/hcaptcha-challenge-pipeline/) [проверено] — getcaptcha, `n`/`c`, motion-VM, siteverify ·
[Implex-ltd/hcaptcha-reverse 2023](https://github.com/Implex-ltd/hcaptcha-reverse) [не проверено] — **устарело** (bundle 1.40.x).

**Съёмка:** актуальный build-hash, поля запроса челленджа на целевом enterprise-сайте (`rqdata`, `host`, `motionData`),
нужен ли интерактивный грид, какой ключ реально нужен для `n`.
**Вердикт: js-runtime** (Node + WASM); на сайтах с серверным гейтом фактически **браузер**.
## 10. reCAPTCHA v3
**Wire (2026-10).** Anchor (`/recaptcha/api2/anchor` или `/enterprise/anchor`) с `k, co, hl, v, size, cb` → в HTML
скрытый `recaptcha-token` → POST `/recaptcha/api2/reload` (тело — **protobuf**) → ответ содержит `rresp` = токен.
Браузер не нужен. `v` (render-версия) надо брать актуальный с `api.js`, иначе скор занижается; PyPasser кеширует
его на час. Enterprise — тем же путём через `/enterprise/anchor`.

Источники: [PyPasser recaptcha-v3.md 2026-10-23](https://github.com/xhossein/pypasser/blob/910c3def3d69e68d42d86ccba5a1b4cf105ee1ed/docs/solvers/recaptcha-v3.md) [проверено] — pure HTTP, anchor, `recaptcha-token`, `rresp`, синхронизация `v` ·
[EvickaStudio/BypassV3](https://github.com/EvickaStudio/BypassV3) [не проверено] — reload принимает protobuf ·
[StackOverflow 2025-04](https://stackoverflow.com/questions/79589776/) [не проверено] — `action` при ручном reload.

**Съёмка:** текущая схема protobuf-сообщения, обязательные поля, что даёт `action`, реальный скор при HTTP-пути.
**Вердикт: HTTP-only.** Ограничение не в протоколе, а в скоре — кодом его не поднять.
## 11. ALTCHA
**Wire (v3, 2026).** Пейлоад — base64 от JSON `{algorithm, challenge, number, salt, signature}`. `challenge` =
key derivation (v1 — SHA-256; v2/v3 — PBKDF2/SHA-256, Scrypt, Argon2) от `salt + number`; `signature` =
HMAC-SHA-256 от challenge на секрете **сайта**, нужна только серверу. Виджет — web component, challenge
создаёт **собственный эндпоинт интегратора** (`createChallenge`): централизованного сервиса, куда можно прийти
с одним sitekey, нет — «100% self-hosted». PoW считается на чистом Python, JS не нужен, но challenge надо взять
со страницы/эндпоинта конкретного сайта.

Источники: [altcha-org/altcha README (v3)](https://github.com/altcha-org/altcha) [проверено] — self-hosted, Argon2/Scrypt, web component ·
[altcha-lib README](https://github.com/altcha-org/altcha-lib) [проверено] — `createChallenge`/`verifySolution`, v2 API + `altcha-lib/v1` ·
[altcha.org/docs/server-tldr](https://altcha.org/docs/server-tldr/) [не проверено] — состав пейлоада, HMAC ·
[CapSkip ALTCHA 2026-09](http://capskip.com/altcha-selenium/) [не проверено] — обзор интеграции.

**Съёмка:** какой атрибут отдаёт challenge (`challengeurl`/`challenge`), формат ответа эндпоинта, стоимость KDF.
**Вердикт: HTTP-only**, но только при наличии URL челленджа с целевой страницы. По одному sitekey — нет.
## 12. Friendly Captcha
**Wire.** Две несовместимые версии. **v1** — `api.friendlycaptcha.com/api/v1/*`: пазл забирается по sitekey
(`GET .../api/v1/puzzle?sitekey=…`), решение — PoW на **blake2b-256** по открытой спецификации `friendly-pow`,
ответ в поле `frc-captcha-solution`; чистый Python. **v2** — `global.frcapi.com/api/v2/*`, сессионная модель
(`agent_id`, `sess_id`, `signals`), спецификация не публична: скорость/дистанция мыши, тайминги клавиш,
`event.isTrusted` (не подделать через `dispatchEvent`), детект патча нативных функций, ориентация устройства.
Автор единственного публичного v1-солвера прямо пишет, что v2 требует реального браузера с человекоподобным
вводом и всё равно не гарантирует прохождение.

Источники: [friendlycaptcha-solver (PyPI) 2026-09-13](https://pypi.org/project/friendlycaptcha-solver/) [проверено] — v1 vs v2, endpoint, blake2b-256, `fetch_puzzle(sitekey)`, разбор сигналов v2 ·
[FriendlyCaptcha/friendly-pow](https://github.com/FriendlyCaptcha/friendly-pow) [не проверено] — открытая спецификация пазла ·
[Capsolver FriendlyCaptcha 2026-11-03](https://docs.capsolver.com/de/guide/captcha/FriendlyCaptcha/) [не проверено] — `websiteKey`+`websiteURL`.

**Съёмка:** версия на целевом сайте (по имени скрипта: `friendly-challenge` = v1, `@friendlycaptcha/sdk`/`site.min.js` = v2),
текущая сложность, принимает ли сервер решение без сигналов.
**Вердикт:** v1 — **HTTP-only** (challenge по одному sitekey); v2 — **браузер**.
## Сводка

| цель | вердикт | ключевой публичный источник 2026 |
|---|---|---|
| GeeTest v3 | HTTP-only после порта 3 функций | cnblogs 2025-12, jishuzhan 2025-11 |
| GeeTest v4 | HTTP-only | GeekedTest v1.9.9-53b07e (2026-10-30) |
| Turnstile | браузер | cloudflare-turnstile-reversed / PyPasser |
| cf_clearance | браузер | cloudflare-skip (2026-10) |
| DataDome | tags HTTP-only; кука js-runtime; сессия браузер | dfp-redteam-index (2026-11) |
| Kasada | js-runtime | kasada-vm (2026-06), dfp-redteam-index |
| PerimeterX | sensorless HTTP-only; press js-runtime | PX_SDK_Reverse_Engineering (2026-05) |
| Imperva | js-runtime | hyper-sdk (2026-11) |
| hCaptcha | js-runtime, на злых сайтах браузер | hsj-hsw-reversed (2026-06) |
| reCAPTCHA v3 | HTTP-only | PyPasser v3 (2026-10) |
| ALTCHA | HTTP-only при наличии URL челленджа | altcha v3 / altcha-lib |
| Friendly Captcha | v1 HTTP-only; v2 браузер | friendlycaptcha-solver (2026-09) |
## Что в наших файлах уже неверно

- GeeTest v3/v4: закрыто в `feat/geetest` (2026-11-15) — v3 использует модифицированный base64,
  H/getAA и answer-`w` без RSA-хвоста; v4 получил публичный RSA-модуль, три группы срезов
  `lot_number`, `payload`/`process_token`/`payload_protocol`/`risk_type`/`pt`,
  `userresponse = left/1.0059466666666665+2` и GET `/verify`.
  Осталось снять с live: `pt=2` (не публичен), `ep.v` v3 и актуальность алфавита энкодера — см. runbooks.
- Билдеры `{akamai,kasada,datadome}`: FILL-стабы закрыты прогоном вендорского скрипта в V8
  (`engines/{akamai,kasada}/harness.js`); sensor_data и `/tl`-тело берутся из самого скрипта, а не собираются
  по догадке. Kasada cd честно только в payload-mode: ответы PoW не восстанавливаются из wire — `engines/kasada/pow.py`.
- Билдеры `{perimeterx,imperva}`: ключевые поля (`ft`/`tag`, reese) уже описаны публично —
  FILL'ы закрываются чтением, а не реверсом с нуля.
## Не удалось прочитать

- deepwiki.com/xKiian/GeekedTest/* — 429, Vercel Security Checkpoint (нужен JS).
- brightdata.com/blog/.../how-to-bypass-kasada — тело не отдаётся без JS, только сниппет.
- docs.hypersolutions.co/api-reference/* — контент рендерится на клиенте; факты взяты из README SDK.
- altcha.org/docs/* — 404 на прямые пути; факты из README пакетов.