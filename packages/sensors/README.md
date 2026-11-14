# captcha-sensors

Per-antibot-engine sensor payload builders. HTTP-first path to clearance
cookies / tokens without a browser.

Each engine subclass of `SensorBuilder` consumes a `BuildContext`
(fingerprint, behavior payloads, scraped challenge JS, site URL, timestamp
window) and emits the opaque blob the engine expects — `sensor_data` string
for Akamai, JSON for DataDome, `x-kpsdk-cd` / `x-kpsdk-cr` for Kasada,
`_px`/`_pxhd` for PerimeterX, and so on.

Schemas mutate per engine release. The skeleton here fixes the **interface**
and leaves the field tables behind `# FILL: <next step>` markers that point at
`runbooks/<engine>.md`. Pour live-reversed layouts there and keep the
registry-level API stable.

## Flow

```
SolverContext.sensors.get(AntibotEngine.AKAMAI).build(ctx) -> payload
```

- `register_builder(engine)` auto-registers the subclass on import.
- `get_builder(engine)` returns the singleton instance.
- `build()` is async: real impls will hit js-runtime, crypto, behavior
  generators concurrently.

## Adding / refreshing an engine

1. Dump a fresh challenge with devtools and a real session.
2. Fill the field table in `runbooks/<engine>.md`.
3. Translate it into `_pack_*` helpers inside the engine module.
4. Remove the `SensorError("not yet implemented ...")` fallback.
5. Replay against the runbook's recorded fixtures.
