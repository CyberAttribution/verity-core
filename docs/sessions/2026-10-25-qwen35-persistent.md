# Bounded Qwen3.5 persistent-execution checkpoint

October 25, 2026, America/New_York. Session label
`DEV_102526_LAMBDA_1xA100_40GB`; one A10040GB, one serialized model endpoint.
This is scoped development evidence. B15.5–B15.8 customer acceptance and sustained
dual-GLM acceptance remain open.

## Recovered authority and baseline

Canonical Library file `libfile_d0143a6e52e08191ad8e798276e98d7f` opened as
`SwarmTracker_Verity_Development_Backlog_v33.md`; Library name search found no
later revision. Its original downloaded SHA256 was
`3f13cd188072367996565bdb1ee250a0370be1ecfe8837ce7979339fb3c9d656`.
Retained Mini operating records contained v30, an explicitly older copy. The
October 24 consolidated handoff, October 25 force-quit audit and execution
operations were read. Actual MacBook checkout and execution installation were
clean at `f660309854cced56a1bf5eae39dc51facce8768d`; no subsequent working changes
or retained profiles were reset. The established queue still belongs to
`MacBookPro.lan`; both Mini alternatives stayed inactive.

The Lambda console showed `DEV_1x_A100_40GB_LAMBDA`, `150.136.40.188`,
`gpu_1x_a100_sxm4`, `us-east-1`, selected key `swarmtracker-mac-mini`, Running.
Provider UUID, allocation rate and billing start were unavailable. The selected
public key exactly matched the M2's existing `lambda_swarmtracker` public key.
An initially absent host pin was resolved using the authenticated Lambda
Jupyter console; its ED25519 fingerprint independently matched the network key:
`SHA256:Od2GvbaxzKXcebmovc2AfHIkskMQCv6lcC9Ho9fXAEQ`.
Strict host checking and disabled agent forwarding were retained. Kevin
separately approved the MacBook's existing public key temporarily, with no
private-key copying. Key restrictions, local-only forwarding and loopback bind
kept the model private. That temporary entry and model tunnel were removed at
closeout; the temporary SSH drop-in was retired.

The official checkpoint is `Qwen/Qwen3.5-4B`, exact revision
`851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`, resolved before download. Only that
checkpoint and its tokenizer/configuration/license artifacts were downloaded.
Actual GPU: NVIDIA A100-SXM4-40GB,40960MiB; driver580.105.08. Runtime:
Python3.10.12, installed Torch2.7.0/CUDA12.8, Transformers5.3.0, BF16 oncuda:0.
All loaded parameters were checked on CUDA; a CUDA tensor operation passed.
DeltaNet uses the pure Torch implementation, with no optional optimized kernel
build. This is a GPU kernel fallback, not an observed CPU model fallback.

Pinned setup repaired old Pillow and Jinja dependencies. Two failed wrapper
requests were retained:001 failed template compilation before generation;
002 generated text but failed scalar-EOS finish bookkeeping. Neither identity
was replayed. Baseline003 completed in3.861seconds,76prompt/67completion tokens,
`stop`, complete SSE/DONE. It gave the supplied date, one copied underlying
origin and unknown outcome. This synthetic evidence fixture is not a real trial
finding or held-out model-quality benchmark. Serving passed within roughly
20minutes of bounded preflight. Inherited `pip check` warnings about pygobject/
pycairo and ipykernel/debugpy remain recorded; the exercised serving imports and
requests passed without rebuilding those unrelated packages.

Tokenizer inspection reported `TokenizersBackend`; tokenizer.json SHA256
`5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42`,
tokenizer_config.json
`316230d6a809701f4db5ea8f8fc862bc3a6f3229c937c174e674ff3ca0a64ac8`.
The chat-template hash is
`a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715`.
Its actual generation branch renders an empty think block when
`enable_thinking=False`. The new explicit profile omits GLM reasoning fields,
uses that option and deterministic generation, and limits wire output to384
tokens. Runtime payloads and identities are retained separately from the
historical2048-token GLM-shaped task contract. No alias bypass was used.

## Persistence, adjudication and interruption

Execution source was committed as `4df76650391fba19482db76e441a8a8bcbe99d67`.
The actual MacBook Prefect3.8.8 server/process worker and existing active
coordinator.db were reused. No new queue or scheduler was introduced. A paused,
expired prior allocation was explicitly renewed through the audited API, which
retains both old and new limits. Existing historical task/request identities
remain in the same database.

Twenty finite assignments bound task/agent/session, source/input hashes, model,
profile, expected output, expiry and90-second request limits. Inputs were public
Qwen configuration/template evidence and sanitized invented copied-source
fixtures; no screenplay, credentials or personal financial information was sent.
Multiple logical lanes share one endpoint; the wrapper serializes GPU work.
These assignments support B05.3/B18.5/B25.5 inspection and limited quality checks,
not a real article-research acceptance or dual-GLM test.

Initial flow:`e73e1c83-5ecb-45d9-b79a-bef5c34f3c8d`.
Second flow:`55952b0f-a5b3-4665-988e-a8ed8606d1de`.
The first timed interface test was invalid: shutdown took effect after eligible
work had already finished. Its logs remain; it was not counted as disconnected
progression. The corrected helper stopped only the identified operator-transport
processes and resumed the already prepared Prefect supervisor, with no per-task
dispatch commands. Desktop Commander independently showed the MacBook offline.

Recorded transport stop:2026-10-26T01:29:12.458UTC
(October25,21:29:12EDT). Reconnect began01:30:27.486UTC. Three real server requests
started01:29:22.709,01:29:25.253 and01:29:28.789UTC and completed within that
interval. Their IDs are respectively
`7cd4e2c83fcc4cc99f007c3d481ba23f`,
`08f5857ca9724dac8d7c0242e8a8c0dc`,
`75843c86838d4b38b40f4f68a6af97e4`.
Reconnection showed the same queue, prior results, pending decisions and held
dependency. This proves a Desktop Commander operator-transport interruption;
the execution-chat turn did not end, and sleep/logout/reboot were not tested.
The coordinator Mac was kept awake.

`Q35Identity` was independently verified against config and server identity;
only then did the existing flow release `Q35VerifiedSuccessor`. Independent
requests advanced before that review. Expiry, changed authorization and pause
held work without requests. The disposable lost-ack result was saved on the
server, but the client ledger remains unresolved/attempts1 and its same-lane
successor remains held. No result was blindly adopted or replayed.

Final queue disposition:

| Category | Count |
|---|---:|
| Independently accepted scoped results |5|
| Complete responses, independently rejected |11|
| Unresolved lost acknowledgement |1|
| Held, never dispatched |3|
| Total assignments |20|
| Unique queue requests |17|
| Duplicate/repeated requests |0|

All16 ordinary responses had complete `stop` status; only5/16 met their narrow
criteria. Failures included confusing layer count with MLP feature width,
misstating the normalization, inventing authority/application/benefit from
configuration, and inadequate receipt/evaluation outlines. Rejected answers
remain completed with rejection evidence; they do not release dependencies.
This is a poor quality result despite successful execution persistence.

Server totals, including setup:20unique starts,18retained complete results,
two retained setup/protocol failures, zero duplicate IDs. The17queue generations
used1712completion tokens and80.622seconds of measured server generation time.
This does not measure sustained GPU utilization or end-to-end operator time.
Long gaps were setup/profile preparation, the invalid first interruption test,
explicit review/dependency holds, uncertainty/gate holds, and preservation;
they were not useful inference. There were two finite batch launches, one
explicit prerequisite release and one interruption-test retry; no chat command
was needed for each automatic successor. Human setup/transport assistance was
requested; active operator minutes were not measured.

The session was paused, then stopped after useful work. Reconciliation remained
readable. The owned Prefect worker restarted849->31639; fresh stopped-session
flow`406fe3dd-645d-49b6-8992-09b07f3dfe70` completed without inference. All task,
request, response, disposition and review fields matched before/after. This
restart occurred after quiescence, not during an in-flight request. Queue GPU
telemetry is explicitly unknown; actual GPU samples are separate evidence.

## Steering disposition

Authorization: Kevin conditionally authorized the sourced B25.5 candidate after
its semantics and persistence prerequisites were resolved. No guessed profile
was authorized as a replication.

Application: no activation hook or intervention was installed. Both reported
feature indices fit intermediate width9216 and exceed residual width2560, but
structural plausibility does not resolve original checkpoint, index base,
activation/gating site, add-versus-replace, normalization/calibration axes,
prefill/decode/token scope or combined-dose meaning. Direct primary X retrieval
returned403. The precise blocked specification is retained privately.

Effect: unmeasured. No new positive or null steering-effect claim follows from
baseline quality. Preserve the earlier residual-output experiment's null benefit
only in its original scope. No final intervention evaluation or matched-control
comparison was run; model-produced outlines were rejected as inadequate.
No patent-coverage or general behavioral-control conclusion is made.

Both weight-shard hashes matched again after inference:
`26a93f066e1916adb13453dae5a0c707c0fbc71299ed98779571a907b8e74c61` and
`cb544bd9bfae93dc59b0f22b292f5933573854a7f9b97835c67060d7d910e188`.
The model process stopped01:35:37UTC; a subsequent sample confirmed0MiB/0percent,
no endpoint listener and no running containers. No container was created or
stopped in this host-process experiment. Provider termination is separate.

## Preservation, checks and billing

Code at4df7665 passed35focused prepared-execution/coordinator tests on the
MacBook, one existing dependency deprecation warning. The committed-source run
also passed35. No full regression or native customer-interface acceptance was
performed for this change. The retained wrapper is experimental infrastructure;
its exercised path is covered by the actual baseline/queue records, including
failures, rather than a general serving compatibility claim.

Unique Lambda source, runtime lock, launch/listener evidence, all request records,
configuration/template hashes, failures, final weight hashes and teardown are in
`qwen35-final-checkpoint.tar.gz`, SHA256
`12455c1c64357bef3af8c9c04f141e147efb280aa64705a4d4c622223e8707ed`.
The M2 independently matched the archive and every final-artifacts manifest
entry under
`/Users/swarmtracker/Documents/ChatGPT/ST_SOFTWARE_M2/private/session-20261025/`.
Earlier baseline and queue checkpoints were independently hash-matched there.
Weights remain reproducible by pinned revision and hashes and are excluded from
the evidence archive. No unique Lambda artifact requires continued rental.

The original MacBook active queue remains at the established October24
`macbook-execution/active-queue/coordinator.db`; its online final checkpoint has
SHA256`c32e8de21b6b79cd7bbd30b17bf58ad4ad3f91715340032ed789e3e5ba8b2848` and
passedSQLite integrity. Private inputs, batches, streams, transition/audit
exports, interruption witness, failed first test, adjudication, process receipts
and Prefect metadata backup are under the October25`qwen35` private directory.
Sessions record`DEV_102526_LAMBDA_1xA100_40GB` contains131append-only events in
the existing development-sessions store, with an independent export and online
database snapshot. Private evidence stays out of Git. Secondary Mac/RAID backup
verification is a local follow-up, not a reason to retain this Lambda instance.

The authorized cumulative ceiling isUSD5 including setup/idle, withUSD1 reserved
and dispatch deadline02:40UTC. The estimate uses referenceUSD1.99/hour and a
conservative00:45UTC start proxy, earlier than observed guest boot around00:53.
The running allocation did not appear in the provider Usage table; its actual
rate/start and final charge remain unknown. Taxes, retained storage/network,
billing rounding and any provisioning-before-proxy exposure are unknown.
At01:34:28UTC the conservative estimate wasUSD1.641; billing continues after
model stop. Estimates cannot guarantee the provider's final bill. TheUSD4
threshold had not been reached when inference ended.

Ready for manual termination: `DEV_1x_A100_40GB_LAMBDA`, IP`150.136.40.188`,
type`gpu_1x_a100_sxm4`, region`us-east-1`. No automatic shutdown or replacement
allocation was performed. Confirm termination and final charges in Lambda.
