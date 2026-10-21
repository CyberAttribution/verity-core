# Governed external inference next step

October 21, 2026. This is an implementation plan, not a working external adapter
or permission for credentials, paid requests or provider login. API-key and
provider-managed sign-in remain separate intended paths. Select a first provider
only after checking its current official transport, consent and usage contracts.
Consumer subscriptions, identity login and API inference grants are distinct.

## Existing pieces and missing behavior

`CredentialManager.resolve(connection_id, expected_digest=...)` retrieves a
confirmed profile/provider/origin/configuration-bound secret through the native
helper, internally only. It rejects unknown saves and changed connections; it
does not validate a key with a provider. The current Personal Team helper is
development-only and expires October 27 at 23:59:16 UTC. Rotation, release helper
trust and provider authorization remain open.

`BudgetLedger.reserve`, `mark_dispatched`, `mark_unknown`, `reconcile` and
`release_undispatched` persist exposure and one dispatch authorization. They do
not constrain independent calls or cap provider charges. The spending page now
retains offline scenarios, explicitly supplied prices, checked date, assumed
input/output counts and exact microUSD estimates. These scenarios are not
actual provider usage and must never settle a reservation by themselves.

Local inference already retains effective configuration, task identity and
receipts. It has a local-only transport; adding an external connection or key
must not route through it or silently activate cloud fallback.

## First adapter transaction and evidence contract

1. Prepare an immutable request review binding task/session/agent/connection,
   provider origin and model/revision, prompt and effective-config digests,
   exact data-sharing scope, purpose, pricing snapshot and maximum planned
   output. Display what leaves the Mac. Require explicit per-job consent;
   approval of changed content, destination or settings must be renewed.
2. Reserve integer estimated exposure against the reviewed policy digest and
   UTC/session limits. Persist the request ID and consent before any transport.
   A unique task/request ID can authorize only one dispatch. Record the price
   unit, source, checked date and rounding convention separately from usage.
3. Recheck consent, connection disablement, agent pause, configuration and native
   credential binding immediately before dispatch. Design a shared config guard
   that supports external destinations; do not blindly reuse the local health
   requirement. `resolve` currently acquires that config lock itself, so calling
   it inside another write guard would deadlock. Refactor an internal locked
   retrieval path with one documented lock owner; retain the public method's
   guard. Establish and test config → credential metadata → budget lock order,
   never hold a budget write transaction across a network call.
4. Commit `mark_dispatched` before the one bounded transport attempt. Use the
   exact approved HTTPS origin, no redirects or inherited proxies, strict byte
   and nesting limits, bounded output and timeouts. Never log authorization
   headers, key values or raw provider exceptions. A local timeout after the
   send boundary is unknown, not proof of zero charge or cancellation. No
   automatic retry/fallback. A provider idempotency key is useful only if its
   semantics are officially supported and independently tested.
5. Retain response/request identifiers, returned model and completion state,
   raw-response digest and provider-reported usage with attribution. Reject
   invalid, partial or mismatched usage. Token counts multiplied by a price are
   still a calculated cost estimate; they are not a reported charge. Extend
   reconciliation with explicit provenance and separate calculated/reported
   charge fields before wiring this adapter to the ledger's `actual_microusd`.
   Missing/uncertain usage keeps the hold. Corrections append evidence; they do
   not rewrite earlier bills or reopen dispatch.
6. Atomically persist the terminal result and sanitized export/checkpoint.
   Export exact consent/configuration/pricing references, reservation and
   dispatch transitions, usage provenance, observed outcome and uncertainty.
   Independently reconstruct the receipt; integrity does not establish semantic
   correctness, authenticated operator identity or provider-account enforcement.

## Ready offline work and release gates

Implement the consent/request schema and explicit reconciliation provenance
first. Then test an injected transport or disposable loopback fixture; no real
provider endpoint or key is required. Cases: missing/changed consent; stale
config; pause/disable before dispatch; concurrent same task; reserve denial;
native unknown/missing/changed binding; crash before and after committed send
authorization; timeout; redirects; oversized/deep body; mismatched model or
request ID; absent/negative/duplicate usage; calculated cost versus reported
charge; overrun; restart; conflicting correction; receipt reconstruction and
secret scanning. Assert zero send for every pre-dispatch denial and one attempt
for authorized work. Retain uncertain holds through all restart paths.

Real API-key acceptance needs separately authorized disposable key entry and a
bounded paid request with confirmed billing source. Provider-managed sign-in
needs its own approved registration, browser consent/state/callback, scopes,
local refresh/revocation and grant-to-inference acceptance. Neither path is
enabled by this plan. Live activation, separate OS identities/direct denial,
rotation, fallback/cancellation, authenticated approvers, pristine-account
release signing/notarization/Gatekeeper remain distinct gates.
