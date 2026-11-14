# captcha-queue

Redis Streams queue and task state store that sit between the API gateway and the solver workers.

## Layout

- **Producer** — gateway side. `XADD` a `TaskEnvelope` (msgpack-encoded) onto the lane stream the dispatcher picked, with `MAXLEN ~` trimming.
- **Consumer** — worker side. `XREADGROUP` with blocking, `XACK` on success, `XCLAIM` on min-idle (stuck entries), `XADD` to the dead-letter stream once the retry ceiling is hit.
- **TaskStateStore** — hash per task id (`status`, `result`, `error`, `started_at`, `finished_at`), 24h TTL. The gateway reads this when the client polls `getTaskResult`.
- **pick_lane** — pure function mapping a `Task` to one of `http | cv | protocol | browser`.

## Lanes

| lane       | who runs it                                                          |
| ---------- | -------------------------------------------------------------------- |
| `http`     | token solvers (recaptcha, hcaptcha, turnstile, funcaptcha, ...)      |
| `cv`       | vision + audio + grid/click/rotate/slider/bbox/drag                  |
| `protocol` | pow / proof-of-work style (friendly, altcha)                         |
| `browser`  | anything the client pinned to `SolveMode.BROWSER`                    |

## Keys

```
<prefix>:stream:<lane>       lane stream (XADD / XREADGROUP)
<prefix>:dlq                 dead-letter stream
<prefix>:task:<id>           per-task hash (TaskStateStore)
<prefix>:stats:<type>        per-type counters
group:lane-<lane>            consumer group name
```

Default prefix is `captcha`; override through `Settings.redis_prefix` or the constructor arg.
