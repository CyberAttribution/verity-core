# Governed decision review

Verity can preserve a successful single-text analysis as a decision run, then
record operator approval or rejection before a fixed local simulation. Active
Agents and Governance link to the preserved participants, evidence, findings,
policy, authorization and outcome. The existing runtime is not required for the
synthetic demonstration.

## Try the demonstration

1. Open Active Agents or Governance, then **Open decisions**.
2. Create a synthetic case using policy version 1. Both participants, delegated
   check and conflicting findings are explicitly labeled deterministic fixtures.
3. Select **Test backend denial** before approval. The request returns HTTP 403
   and the denied attempt is preserved. No simulation completes.
4. Enter a reason and record approval. Execute the approved simulation once.
   Another attempt is denied; a stale page returns HTTP 409 and must reload.
5. On another run, reject the action. Execution is denied.
6. Create a version 2 run. That policy denies execution even after approval;
   earlier runs preserve version 1.
7. Export the decision history and save its checkpoint separately.

The only implemented action is `simulate_sender_block`, with a fixed
`sender@example.invalid` target. The simulator is its durably recorded outcome;
it cannot block a real sender or execute a network/tool action. Analysis review
uses this same test target, regardless of the actual submitted evidence, and
does not claim that the model recommended blocking.

## Preserve an existing analysis

Complete a successful single-text analysis through Analyze, then select
**Preserve for decision review** on the report. Confirm preservation to copy
its input text, hashes, local finding, runtime event reference and self-reported
runtime identity into a persistent run. The short-lived report must still be
available at confirmation. After preservation, the run survives app restarts.
No second participant, live delegation or conflicting finding is invented for
this path. Automatic capture of arbitrary external agents is not implemented.

## Persistence and enforcement

`governed-decisions.db` lives beside the configured investigation database
(`NODE_UI_DB`), so normal application-data configuration is respected. It is a
separate SQLite WAL database; the investigation schema and legacy runtime ledger
are unchanged. Each operation opens its own connection. `BEGIN IMMEDIATE`
serializes authorization/execution writes, and every mutation verifies the
current chain and checks the submitted head hash. Approval is bound to the
preserved action, policy, participants, evidence, findings and recommendation.
An authorization decision is final for its run. Rejection and policy denial
cannot be overridden by a later form field.

Attempt and result commit atomically with SQLite `synchronous=FULL`. An audit
write failure rolls back the simulated outcome. This atomicity applies to the
fixed simulator; it is not an exactly-once guarantee for future external actions.
Corrupt or unverifiable runs cannot execute or export. A recent-run view flags
the integrity failure instead of treating the run as valid.

New mutation routes require an instance-specific operator form token, an exact
loopback Host and same-origin request checks. Cross-site requests, duplicate
fields, unsupported body types and oversized forms are refused. Restarting the
single worker invalidates old form tokens. This is a local intent boundary,
not a login, authenticated human identity, host-compromise defense, or a
security retrofit of legacy routes. Multiple web workers are not supported by
the per-process form token.

## Verify outside the application

With the Core package available, the verifier uses only Python's standard
library and needs no running app, runtime, SQLite database or model:

```bash
python -m verity_core.trust.verify_decision run.decision.json
python -m verity_core.trust.verify_decision run.decision.json --checkpoint retained.checkpoint.json
```

The verifier checks evidence text hashes, the Trust Record chain, supported
lifecycle transitions, action/approval binding, outcome linkage, the complete
record digest, and checkpoint entry count/head. Exit status is zero only when
verification passes. JSON input is depth/size bounded and duplicate keys are
refused. Keep checkpoints independently; a checkpoint bundled with its own
rewritten record cannot authenticate that record. Recomputed full rewrites or
valid suffix removal require a separately retained checkpoint to detect.

These checks do not establish evidence truth, participant identity, complete
event capture, immutable storage or Byzantine resilience. This demonstration
is application-level governance and implements no internal transformer
forward-pass intervention. Decision approval does not authorize cloud evidence
submission; the existing cloud-assist gates remain separate.

## Validation for DEV_101426

- Linux Python 3.12: 613 passed, 2 skipped, one pre-existing Starlette/httpx warning.
- MacBook Python 3.14: 613 passed, 2 skipped, the same warning.
- Chrome on the MacBook exercised denial, approval, one simulation, export,
  Active Agents links and the Governance view with no JavaScript page errors.
- After stopping and restarting the MacBook worker, the exported decision
  history was identical. The separate verifier accepted it against the retained
  checkpoint.
- Actual single-analysis intake-to-run capture was checked with an injected
  local runtime response. No live OpenClaw inference or cross-Mac delegation was
  demonstrated in this session. Native installation remains separate work.
- Two GLM reviews completed with explicit PASS verdicts on the final implementation:
  state machine/SQLite concurrency and HTTP mutation security. These are scoped
  static model reviews, not a security certification. Earlier truncated reviews
  and one rejected request format are retained as incomplete attempts.
