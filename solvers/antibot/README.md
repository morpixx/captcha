# captcha-solver-antibot

One package, one plugin per antibot engine. Each solver's job is: resolve the
sensor builder from `ctx.sensors`, feed it a `BuildContext` assembled from
`task.payload` + `ctx.fingerprint` + behavior, then POST the resulting payload
to the engine endpoint through `ctx.http`.

Live RE lives in `packages/sensors`. This package only wires the HTTP side.

| engine | solver(s) | types | runbook |
| --- | --- | --- | --- |
| Cloudflare managed challenge (IUAM / orchestrate) | `CloudflareChallengeSolver` | `CF_CHALLENGE` | `runbooks/cloudflare.md` |
| Cloudflare Turnstile (sitekey widget) | `TurnstileSolver` | `TURNSTILE`, `TURNSTILE_INVISIBLE` | `runbooks/turnstile.md` |
| Akamai Bot Manager — `sensor_data` POST | `AkamaiSensorSolver` | `AKAMAI_SENSOR` | `runbooks/akamai.md` |
| Akamai SBSD (Bot Manager Premier) | `AkamaiSBSDSolver` | `AKAMAI_SBSD` | `runbooks/akamai_sbsd.md` |
| DataDome (slider + full stack) | `DataDomeSolver` | `DATADOME_FULL`, `DATADOME_SLIDER` | `runbooks/datadome.md` |
| Kasada KPSDK | `KasadaSolver` | `KASADA` | `runbooks/kasada.md` |
| PerimeterX / HUMAN | `PerimeterXSolver` | `PERIMETERX` | `runbooks/perimeterx.md` |
| Imperva / Incapsula | `ImpervaSolver` | `IMPERVA` | `runbooks/imperva.md` |
| Castle | `CastleSolver` | `CASTLE` | `runbooks/castle.md` |
| F5 Shape (TSPD / integrity cookie) | `TSPDSolver` | `TSPD` | `runbooks/tspd.md` |
| CyberSiARA | `CyberSiARASolver` | `CYBERSIARA` | `runbooks/cybersiara.md` |
| Twitch Integrity Token | `TwitchIntegritySolver` | `TWITCH_INTEGRITY` | `runbooks/twitch_integrity.md` |
| BotGuard (Google `f.txt` / `botguard`) | `BotGuardSolver` | `BOTGUARD` | `runbooks/botguard.md` |
| BotDeflector | `BotDeflectorSolver` | `BOTDEFLECTOR` | `runbooks/botdeflector.md` |

Each solver imports cleanly; `solve_http` on an unwired engine raises
`CaptchaError("not yet implemented — live RE needed; see runbooks/<engine>.md")`.
Grep for `FILL:` to see exactly what's missing.
