# October 19 part B development checkpoint

This is an active-session checkpoint, not a provider termination or final bill.
User budget: USD50 target / USD60 ceiling, including setup and idle, with USD10
reserved for validation and handoff. No automatic provider shutdown authorized.
Public rate USD22.32/hour; provider account rate and allocation start unverified.
At 22:29EDT, guest uptime proxy implied approximately USD17.85. No request-level
allocation cost attribution is claimed.

## Reproduced deployment

Existing allocation: eight A100-SXM4-80GB GPUs, driver580.105.08, NV12 topology.
Exact historical80GB recipe recovered rather than substituting a TP8 deployment:

- Repository `wtdcode/GLM-5.3-Flash-AWQ-W4A16`.
- Revision `abd7b07719111f137e1de8a0c1b7e01c11b74d1a`.
- Image `lazymio/vllm-backport@sha256:2627d2a97e7f539b99ac98f511c589f5f520d3fc5630b195aca7f6ef865ead4e`.
- Exact docker argument vectors in `2026-10-19B-reproduction/two-tp4-launch-argv.json`.
- Replica1 GPUs0–3, loopback8000; replica2 GPUs4–7, loopback8001. TP4 each,
  context32768, max sequences8, memory0.90, prefix caching, NCCL Ring/Simple,
  custom all-reduce disabled, bounded FULL_AND_PIECEWISE CUDA graphs1/2/4/8,
  glm45 reasoning and glm47 tool parser. No claim that tools were executed.
- Every review request sets both `reasoning_effort: low` and
  `chat_template_kwargs: {reasoning_effort: low}`. Installed `/tokenize` results
  demonstrated a distinct low rendering. Low does not mean zero reasoning.
- Both readiness requests completed, separate from substantive reviews:
  replica1 1.8216seconds, replica2 1.4156seconds, each22input/5output/0reasoning.
- Observed73501MiB/GPU resident;0%utilization when prepared queues drained.
  No claim of continuous GPU utilization during local implementation.

This80GB result does not retroactively establish why the earlier40GB backend
failed. The earlier40GB trial and this recovered80GB configuration remain
separate reproduction records. No Kolibri/Qwen substitution on this allocation.

## Implemented and validated

- `000c854`: atomic approved context/history capture through durable inference
  reservation; release lock before model request; exact context/specimen/
  instruction binding; stable context_analysis/v0.1 export and outer checkpoint.
- `f775288`: explicit organization-context proposal, review, approval, withdrawal,
  configured analysis, result inspection and export UI. Historical approval is
  not current permission, authenticated identity, source truth or action authority.
- `2b196b6`, `4cf230a`: fresh health required for new agents through both guided
  and generic registration. Legacy configuration retains its prior setting.
  Explicit false is an operator choice. Missing/stale/unavailable/unknown health
  blocks strict routes; final serialized guard independently rechecks.
- `e63e71d`: saving a connection invalidates prior health; an in-flight HTTP
  probe commits only against its captured exact connection version and digest.
  Reproduced old-endpoint observation carryover before fixing.
- `139c4ca`: connection page separates recent request completion from HTTP health,
  labels historical versions and unavailable usage/cost. It inspects only the
  recent30-request window, not complete historical readiness.
- `22029a7`: nullable observation version/hash migration. Unbound legacy health
  becomes unknown until rechecked; current bindings survive restart. Existing
  schema readers stay available while dispatch holds a write reservation.

Full current suite979passed/2skipped in44.55seconds, existing Starlette/httpx
warning. Mac mini independently57focusedpassed in4.06seconds. Initial failing
reproductions and final tests retained privately. HTML/HTTP behavior verified;
no browser visual-inspection or packaged-app acceptance claim for tonight's UI.

## Real product-route acceptance

A disposable synthetic vendor-profile analysis completed through the existing
Mini OpenClaw/Ollama qwen2.5:7b-instruct service in10.0417seconds. This service is
separate from the Lambda GLM reviewers. Catalog digest:
`845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`.
It identified synthetic account5678 versus reviewed account1234. Event
`fd66610e-ec1c-4f2f-a75d-c8901a66a87e`; response digest
`93ddab68b2bb45a4fbff9fdc66b63a6869967156ebee2f5d7883ae0716ab59e2`.
The runtime omitted finish reason; null retained rather than inferred stop.
Token counts unavailable. Export independently verified on MacBook against a
separately retained outer checkpoint. Withdrawal blocked the next request409;
historical export remained verifiable. No payment, file action, real customer
approval, authenticated identity or live invitation involved.

## GLM results and continuity

14 substantive requests:13 completed final answers,1 token-limited,0 timeouts.
Readiness excluded. HealthGuard used2048output tokens, all2048reasoning, length;
no final answer, not an accepted review. Worker automatically advanced to
HealthRegistration without another chat message. Prepared finite queues drain;
notifications are OS requests, not demonstrated chat wakeups or automatic task
invention. Successful final answers still need independent source/tests.

HealthRegistration identified alternate HTTP agent creation without the strict
health default: independently reproduced with failing test, fixed4cf230a.
Its missing-test advice accepted. Earlier dispatch-callback bypass claim was
rejected: trusted Python test callback is not browser-controlled. Self-chosen
replacement export anchors do not defeat a separately retained trusted anchor;
those claims did not establish an import/approval bypass. Correct guard reviews
and scoped planning advice are not additional newly found defects.

## Preservation and blockers

Initial PartB archive0220 independently verified143file hashes,5SQLite integrity
checks, incremental source bundle against retaineda8e4e7c base, and source git
fsck on Mini. Archive SHA256:
`25005352c2a68732ebd614513dd950a6c5498a7c52538330aca902fe22e54053`.
This archive covers through4cf230a; later source is pushed and independently
checked out on Mini, and a subsequent private-evidence checkpoint is pending.
Private prompts, runtime responses, databases and credentials stay outside Git.

B02 still needs distinct local administrator-created identities and observed OS
denial. B03 live AuthKit staging, recipient verification and automatic activation
are unverified; existing offline issuance is not that flow. No live invitation
sent. External provider consent/credentials/billing and original Docker company
record's human decision remain blocked. Same-user/direct-runtime access remains
outside this local routing boundary. Existing installed pilots preserved.

## Customer journey checkpoint, 23:29EDT

Canonical backlog18 reread; restored parent capabilities remain open. Implemented
f9b8e4c: separate activation/runtime pages, configured agent/resource/primary-model
request selection, reviewed-config fingerprint, durable result detail and stable
inference_receipt/v0.1 export with separate checkpoint/offline CLI. Request
terminal state and receipt share one transaction. Unfinished reservation has no
receipt or silent retry. Prompt/instruction fields omitted; output may quote inputs.

55920ac links the same run/task/model/event through Agents, Governance, Sessions
and Environment Map. Map distinguishes declared host/service/resource links from
observed request paths, with no discovery scan or OS enforcement claim. 766ca38
adds guided local OpenClaw model registration and preselected agent setup; it does
not install/download a model, establish health or enable frontier-provider login.
7896ff8 makes an older fixed-date enrollment test use its explicit fixture clock;
production expiry remains exclusive and unchanged.

Current full suite993passed/2skipped,41.82seconds, existing deprecation warning.
Mini independently18focused tests at766ca38; preceding cross-view31focused checks.
Real disposable Mini public-form guided path completed in3.6149seconds, task
B24GuidedJourney20261019B, event0b9430d3-ecc8-4cdd-b43f-8e7d570d4368,
response SHA256d6058a5a8df3f97ef5f363fb3b1369d75dca0464bd1955b8d068eebf30c1c068.
Offline signed activation, relaunch, public resource/model/agent forms, health,
actual response, cross-view links, duplicate refusal, purpose denial and imported
revocation were exercised. Success/denial receipts independently reconstructed
on MacBook with retained checkpoints. Existing Mini Qwen7 remains separate from
rental GLM review models. Finish reason omitted by runtime stays null; usage unknown.
Synthetic specimen only; no payment, file action, live WorkOS or OS isolation.

Fresh ad-hoc native package built from clean55920ac and its headless frozen worker
passed19checks, including public forms, purpose denial, receipt and duplicate
refusal. Worker exited afterward. No native-window visual/pristine-account/
DeveloperID/notarization/Gatekeeper acceptance. The later766ca38 guided connection
page is source-tested separately and is not claimed included in that build.

GLM totals22completed final review answers,2token-limited,0timeouts; readiness
excluded. One independently accepted new GLM defect remains alternate-registration
strict-default omission. Accepted scoped guard checks/advice and rejected findings
remain distinct in private adjudication. RequestFormGate length/partial claim is
not a completed review; actual final guard and a no-dispatch race test contradict
its premise. No automatic acceptance or chat wakeup capability is established.

Both replicas ran parallel independent journey and guided-connection batches,
with both low-reasoning fields audited. Queues drain during local work. The
packaged check ended23:00:25EDT and next inspection was23:22:56EDT; no further
implementation ran in that gap. GPU review idle began earlier22:58:31EDT. Those
billable intervals are included in total estimate, not presented as useful work.
Estimated exposure at23:25 was38.87USD; provider bill/start remain unverified.
Warn at45USD, aim50USD total, ceiling60USD; no automatic provider shutdown.

Initial0220 archive remains valid but older; an updated source/private checkpoint
is being prepared. New test app binary remains on MacBook; reproducible source,
build metadata/logs and private evidence are included in checkpoint scope, not a
claim that a notarized distributable is backed up or ready for customer release.

### Closeout verification correction

The first packaged-worker denial was specifically a connection-health denial,
not a purpose denial; the real Mini receipt independently records the purpose
policy denial. Latest fresh ad-hoc build at clean6df7f28 includes the766ca38 guided
model page and passed21headless frozen-worker checks, including guided connection
registration and accurate denial reason. No model request occurred in the frozen
worker test; real Mini inference and cross-view tests remain separate acceptance.

The2332 backup matched its file manifest and SQLite/source checks, but an added
referential check found the copy routine had reformatted raw response JSON.
Original24response files still matched their recorded SHA256 values. The2332
archive is superseded as the private-evidence backup: a corrected byte-preserving
final checkpoint will verify all raw response hashes, low-setting request payloads,
source-packet bindings, SQLite integrity and source restore on the other Mac.
Retain the earlier checkpoint as history; use the verified final checkpoint named
in the session handoff for restoration. No source data was lost or modified.
