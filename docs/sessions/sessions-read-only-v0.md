# Sessions read-only pilot

The local Node UI exposes `/sessions`, `/sessions/{id}`, and a JSON export. It never submits work or shuts down a provider instance. Sessions use a separate SQLite event store from the narrowly scoped development telemetry store. The default path is next to the Node UI database; `NODE_UI_SESSIONS_DB` overrides it. Event IDs are idempotent, conflicting reuse fails, and corrections append a new event without changing the earlier observation.

Only the local CLI writes observations:

```sh
python tools/session_observe.py create identity.json
python tools/session_observe.py append lambda-20261017 observation.json
python tools/session_observe.py export lambda-20261017
```

Identity must contain `id`, `purpose`, `provider`, `macs` (array), `branch`, and `commits` (array). An observation contains `id`, Unix `at`, `kind`, `source`, and a `data` object. Allowed kinds and fields are defined in `sessions.py`; unknown fields are rejected. For example, a successful provider inventory with no model is:

```json
{"id":"inventory-001","at":1792245600,"kind":"inventory","source":"ssh","data":{"checked":true,"models":[]}}
```

An SSH failure is a `connection` observation with `state: blocked`; it does not establish an empty model inventory. A shutdown request is distinct from provider-confirmed termination. A `confirmed: true` shutdown requires `provider_api` or `provider_console` source. Provider-reported charges are separate from elapsed-time estimates. Estimates require observed rate and billing start, omit tax and incidental resources unless specified, and remain uncertain until provider reconciliation. Do not place credentials, private prompts, or sensitive result contents into labels and notes.

The UI labels observations by source and shows local versus GPU task scope. A running task alone does not establish current GPU activity: a recent running request observation is also required. Missing contact becomes unknown after `NODE_UI_SESSION_STALE_SECONDS` (default 300). Idle and setup alerts use `NODE_UI_SESSION_IDLE_SECONDS` (default 600); the budget warning uses `NODE_UI_SESSION_BUDGET_MARGIN` (default 20 currency units). These are local display alerts, not provider controls.

`python tools/session_collector.py /path/to/inbox` can run under a local supervisor. It consumes small `*.json` envelopes containing `session_id` and `event`, renames accepted files to `.seen`, and prints newly raised alerts to standard output. A restart safely deduplicates an event committed before its file rename. The supervisor and event producers must be configured separately. The collector does not poll the provider or a model; without an independent source writing observations, status goes stale. An instance can continue billing if this app stops, and provider termination must be checked independently.
