# Prepared execution through self-hosted Prefect

This optional development integration uses the existing `Coordinator`, operator tasks, runtime registrations, attempt ledger and acceptance transitions. Prefect supplies a local process worker and a two-thread task executor. It does not choose authority, verify findings, apply patches, merge, provision compute, or retry uncertain inference. Customer distribution and the full B15.5–B15.7 interface remain unaccepted until their complete native matrix passes.

## Isolated installation and owned services

The preferred host is the M2. Create a separate venv using a compatible installed Python (3.13 was exercised), then install `requirements-prefect.txt`. Record `pip freeze --all`, `pip check`, Python/OS and the exact source commit. Prefect 3.8.8 uses Apache-2.0. The initial unconstrained FastAPI resolution failed server import; keep its failure log and the repaired dependency lock. Do not install this environment into the application or existing agent runtime.

Set these environment variables for the server, worker, deployment and CLI. Use absolute paths on the actual host:

```sh
export PREFECT_HOME=/absolute/private/execution/prefect-home
export PREFECT_API_URL=http://127.0.0.1:4200/api
export PREFECT_SERVER_ANALYTICS_ENABLED=false
export DO_NOT_TRACK=1
export VERITY_PREFECT_DEPLOYMENT=1
```

Start the server with `venv/bin/prefect server start --host 127.0.0.1 --port 4200`. An occupied listener is a refusal: identify it; do not adopt or kill it. Check `/api/health` and the owned process manifest. Use a detached process with private stdout/stderr logs and a retained PID/argv record, or the operator's normal user-service manager. Never expose Prefect publicly or substitute a cloud URL. The adapter refuses an absent or different API URL rather than starting an ephemeral server.

Create a **process** work pool named `verity-local-process`, then start the owned worker with `venv/bin/prefect worker start --pool verity-local-process --type process --limit 1`. The worker launches each coordinator flow as its own process; its task runner has capacity two. The Verity database also refuses a second live owner. A server or process-pool worker being alive does not imply the Verity coordinator is executing useful work.

## Prepared assignments and launch

Create bounded operator tasks with `Coordinator.create_operator_task`. Save a batch with `id`, explicit `authorization` evidence and `assignments`. The adapter's exact schema is in `session_batch.py`; the disposable acceptance tool provides an executable example. Each assignment binds the existing task hash, agent/runtime registration, session, source commit and clean source tree, absolute input path and SHA256, context/configuration versions, permitted purpose, exact loopback route/model, expiry, maximum seconds and acceptance responsibility. Expected outputs/dependencies/token bound remain in the existing task contract. These are local operator-controlled bindings, not authenticated external approval or OS containment.

Load with `venv/bin/python tools/prefect_session.py load --db /absolute/queue.db --file /absolute/batch.json`. A finite session deadline and previously configured rate/start/ceiling/reserve are required; loading a batch cannot enlarge them. Local acceptance uses rate zero and no provider spending authority. A configured model name is not inventory or execution proof: the streaming response must report the bound model and complete its protocol. An alias alone does not establish a weight digest.

Deploy with `venv/bin/python tools/prefect_session.py deploy --db /absolute/queue.db`. This creates the local `verity-prepared-batch/verity-local` deployment. Run that deployment through the self-hosted Prefect API/CLI after `resume`; the foreground `run` action is also available for diagnosis. Prefect receives task IDs, hashes and local ledger paths; prompts, streamed reasoning/final text and usage stay in Verity's private queue. GLM requests always forward both `reasoning_effort: low` and `chat_template_kwargs: {reasoning_effort: low}`. The separately selected `ollama-qwen2.5-7b` profile accepts only `qwen2.5:7b-instruct` and omits GLM-specific reasoning fields; it is never selected automatically after a refusal. Ollama 0.15.6 maps a nonempty low reasoning effort to a thinking request, which the existing Qwen model refused. The historical fixed task contract remains retained, while the immutable runtime profile specifies the actual wire semantics. Request payloads, bounded HTTP refusal bodies and diagnostics are private ledger chunks; none count as token progress. The OpenAI-compatible route does not establish unload-after-request behavior. Requests have finite connect and overall deadlines; a client deadline does not prove server cancellation.

The task schema retains the historical GLM read-only action/model-setting contract. A prepared runtime binding explicitly overrides the wire model for approved local acceptance; this does not migrate customer connection records or establish arbitrary adapter compatibility. No source packet is fetched automatically. Source/input changes require a new packet and authorization. Context/configuration version strings bind the prepared snapshot; integration with every live customer configuration store remains separate work.

## Status and operator controls

```sh
venv/bin/python tools/prefect_session.py status --db /absolute/queue.db
venv/bin/python tools/prefect_session.py pause --db /absolute/queue.db
venv/bin/python tools/prefect_session.py resume --db /absolute/queue.db
venv/bin/python tools/prefect_session.py stop --db /absolute/queue.db
```

Pause prevents new dispatch; stop is terminal for that session. Neither proves remote cancellation nor ends provider billing. Rate/start are operator declarations and produce estimates, never provider charges. A full Prefect task response remains `completed` awaiting Verity adjudication. Only an explicit `Coordinator.verify(task_id, actor, evidence)` releases its dependents. A rejected, truncated or uncertain result does not. Independent tasks advance automatically while dependency holds remain. The dispatcher does not generate successor tasks or source packets; the prepared queue must contain useful authorized work.

Set `NODE_UI_COORDINATOR_DB` to the same existing queue for the app on the coordinator host. Overview and Sessions show the exclusive worker, recorded host, ready/blocked/review counts, request IDs, last token/progress, idle interval and operational limits. A missing/inaccessible queue is unknown. Stale in-flight ownership has unknown idle time. GPU utilization remains unknown without separately fresh measurements. `/sessions/queue/status` is a local observation endpoint. This is not acceptance of authenticated cross-Mac operator control; use established authorized access, never relax loopback/trust requirements to reach it.

## Crash recovery, checkpoint and explicit handover

Attempt identity is persisted before HTTP submission. Crashes/lost acknowledgement become `unresolved`; retries are disabled in Prefect and uncertain attempts are never automatically requeued. A lane with an uncertain request holds while another lane may advance. Review endpoint evidence and use the existing reconciliation path. A new request requires a new identity unless the existing bounded no-execution reconciliation explicitly permits a retry. Do not repeat a saved request merely because status recording failed.

Each completed executor future creates a consistent SQLite checkpoint under the queue's `execution-checkpoints/`, with an integrity check and SHA256 receipt. These are local recovery checkpoints, not independent backups. Copy them plus bound input files, exact source/bundle prerequisites, dependency lock and process/config logs through the existing authenticated backup path. Verify hashes and restore/query `PRAGMA integrity_check` on the other Mac. Do not copy or sync a live SQLite file through the RAID.

For handover, pause dispatch, stop the owned worker/coordinator, reconcile every submitted/acknowledged/uncertain attempt, and record evidence. `handover --db ... --target exact-destination-host --file handover-evidence.txt` refuses active ownership or uncertainty and binds the destination hostname. Then `checkpoint --db ... --file new-checkpoint-directory`; copy and independently verify it on the destination. The original database now refuses dispatch on the old hostname. Restore inputs/source with the same bindings or authorize new packets; explicitly resume on the destination. Keep the old services inactive. This offline protocol does not prevent an administrator from running a forked old database; a distributed ownership authority is not claimed. Hostnames are recorded identities, not remote machine attestation.

An operator-station switch should continue using the same active coordinator. It must not initialize a new queue or implicitly perform handover. MacBook-to-M2 authenticated control acceptance is pending when no supported authorized channel is available; do not enable SSH, copy credentials or change pinned host trust as a workaround.

## Acceptance and next rental

`tools/acceptance_prefect_batch.py accept --output /absolute/new-private-directory` exercises the actual self-hosted process pool using two synthetic streaming endpoints. Preserve its receipt, endpoint request IDs, flow/deployment IDs, process events, complete ledger, checkpoints and failures. The fixture evaluator checks literal fixture strings; it is not code or research adjudication. Unit tests exercise source/config/authorization/deadline/spending gates and slow-trickle behavior separately. Screen lock, literal chat disconnection, sleep and reboot are separate claims; report only what actually occurred.

Then run a bounded prepared request through an existing suitable Mini model. Record actual model/configuration/request/output/usage and explicitly adjudicate it. Do not stress the MacBook 7B model or download another model. After initial acceptance, inactive alternative environments can be prepared with the same lock; installation on another Mac is not a demonstrated handover.

The next separately authorized rental must validate this **same** path early against both GLM endpoints: pin source/weights/template/image; authorize a finite rate/start/ceiling/deadline; import multiple useful packets per lane; observe several unique requests and automatic independent successors; retain dependency holds until review; preserve outputs off-host. Loaded weights, isolated utilization peaks and synthetic fixtures do not prove productive dual-GLM operation. Prepare the next source-grounded packets before capacity is exhausted; no automatic patch executor is included.

The portable coordinator, adapter, process worker and SQLite checkpoints can run on a Linux host with the pinned optional environment, local source and loopback GLM endpoints. Native Swift/UI and host credentials stay on the Macs. Validate Linux dependencies and process/restart behavior before claiming that deployment accepted; do not create a separate scheduler or duplicate ledger.

## October 24 acceptance checkpoint

Source `8600479` passed 1,327 tests with two skips and one dependency warning. The actual M2 self-hosted process path completed nine synthetic requests, held dependency acceptance and uncertain work, and survived owned worker/coordinator restarts. Independent restoration on the M1 checked 360 hashes and 172 databases, including intermediate checkpoints and the Prefect metadata snapshot. A clean-exit lease defect found during that restoration was repaired and rerun through the actual process path on the M1: nine requests, eight incremental checkpoints rather than repeated no-op executor churn. These are scheduling results, not accepted research or dual-GLM rental evidence. Later request-profile changes require their own final-source checks. Screen lock, literal chat network loss, sleep/reboot, native remote operator switch and complete handover acceptance remain open.

Desktop profile binding: Finder/Launch Services may start Verity with `/` as
its working directory. The desktop worker and designated native shell bind the
product coordinator to `product-work.db` within the retained data home. Explicit
`NODE_UI_PRODUCT_DB` or legacy `NODE_UI_COORDINATOR_DB` bindings take precedence.
This avoids a read-only `session-data` failure on Agents without replacing the
profile, activation, governed-request stores or connection records. Existing
external queue databases remain explicit bindings; this is not an automatic
migration or a cross-host queue handover.
