# B19 proposed organization-context boundary

Status: design only, October 19, 2026. No organizational profile, trusted-context
store, authenticated approver, connector, or payment executor is implemented here.

## Existing code and reuse

At 7a801db, Atlas is an evidence/investigation graph and export surface
(app.py:_atlas_bundle and atlas_page, templates/atlas.html, static/atlas_graph.js).
Its rendered epistemic status is presentation, not authority to supply trusted
organizational policy. Preserve the shared renderer and existing graph contract.

CustomerConfig stores immutable expected machine/connection/agent/resource/link
versions. Its provenance is explicitly operator-entered and unconfirmed. Do not
silently reuse those descriptive records as approved organizational facts.

RunStore.authorize demonstrates expected-head comparison, BEGIN IMMEDIATE,
immutable decisions and a Trust Record audit. That API authorizes the fixed
simulator, not organization-context updates. Reuse its transaction/binding pattern,
not its action permission. Session coordinator work approvals likewise concern
workflow generations, not context facts.

evidence_check.context_ref can describe profile/version/hash/approved status,
but that field alone proves no approval. CompanyWorkStore.attach_check currently
rejects every context reference because no authoritative context store is connected.
Keep that denial until authoritative lookup and binding are implemented.
source_assessment deliberately supplies context_ref=None.

## Small authoritative context store

Propose a bounded ContextStore using the existing local SQLite and Trust Record
patterns. Keep proposal records and approval events immutable; maintain a current
approved-version pointer transactionally. Do not introduce editable Markdown as an
authority source or a second graph engine.

Each version contains profile ID, version, parent approved-version digest, schema
version and canonical body digest. Each bounded entry has stable ID, type
(person/vendor/domain/system/relationship/policy), fact/assumption/proposal status,
source reference and passage/artifact digest, checked date, review-before date,
and missing/conflicting qualification. Actual limits belong in implementation
tests before importing real profiles.

Proposed operations:

| Operation | Binding and effect |
| --- | --- |
| propose | Snapshot bounded entries and parent approved digest; never activates. |
| review | Show exact proposed version, sources, missing/stale/conflicting entries. |
| decide | Require expected proposal digest and expected current approved digest; explicit approve/reject. |
| active_snapshot | Resolve only the current approved pointer and validated immutable body/digest. |
| supersede | Explicit new decision; preserve the previous body and approval history. |

Approve/reject and pointer update must share one BEGIN IMMEDIATE transaction.
Compare both the proposal digest and current approved parent inside that
transaction. A decision based on an older parent fails with a conflict rather than
overwriting a newer approval. Replaying a final decision must not append another
approval or change the pointer. Reject leaves the current approved version intact.

A local operator label remains unauthenticated. This store can record deliberate
local review; it must not claim enterprise role enforcement or OS isolation.
B02 and a separately implemented operator identity boundary remain dependencies
for stronger access claims.

## Analysis and action binding

At a governed analysis reservation, resolve an authoritative approved snapshot and
record its exact profile/version/digest and freshness qualifications in the run.
Model/external content may propose a new version but cannot update the pointer.

An analysis already using a snapshot may finish after a context update; its result
retains the old version and shows that it is no longer current. Do not silently
rebase the result. Any later consequential action must recheck its own bound
approval, action parameters, context version and current policy before execution.
Context approval itself grants no action permission.

Evidence export must retain the context reference and enough approved body/history
to reconstruct its digest and recorded local approval against a separately
retained checkpoint. An imported approved-status string or graph styling must not
substitute for authoritative lookup. Source provenance/approval does not prove
factual truth, freshness, authenticated identity or authorized action.

## First product slice and acceptance

Use one disposable manually prepared vendor profile, with no real customer data.
The existing review journey should show Organization, proposal versus active
version, sources, qualifications and an explicit review decision. Atlas may render
the same referenced entities; operators need not manage folders or checker models.

Before a real profile: test proposal nonactivation (including forged approved
metadata), wrong digest, stale-parent concurrent approvals, reject/supersede,
replay, restart, historical export reconstruction, and missing/stale/conflicting
entries. A vendor/payment-detail mismatch fixture must surface a discrepancy for
review and execute no payment. Broad connectors, automatic profile maintenance
and a factual-truth guarantee remain outside this slice.
