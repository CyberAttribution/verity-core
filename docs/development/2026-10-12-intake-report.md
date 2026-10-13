# Verity intake-to-report checkpoint — October 12, 2026

Base: accepted commit 6d12b12e18e197b0056240dc4b411b6da5d04734.
Branch: glm/node-service-authorization.

The existing Node UI remains the local intake surface. Analyze accepts pasted
text or browser-read text files; Media accepts a local media upload. Local
extraction and analysis produce HTML and downloadable JSON/Markdown evidence
reports. The separate Governed Cloud-Assisted radio selection authorizes a
bounded supplement for one job only. Only the exact submitted value "1" is
accepted by the UI, and only boolean True is accepted by the service boundary.
No authorization carries to another job.

Text selection validates every item before policy/provider access, including
items excluded by the selection cap. Up to eight excerpts share an aggregate
6,000-character budget; fixed prompt text and bounded labels/type headers are
separate overhead. Unicode bytes may exceed the character count. The manifest
records exact UTF-8 excerpt size, excerpt/source character counts, excerpt hash,
full-source-text hash and source index; it does not contain raw excerpts.
Budgets may be tightened, not widened.

Cloud execution disclosures distinguish local_only, denied, input_blocked,
audit_blocked, unavailable, cloud_succeeded, cloud_failed and
authorized_not_attempted. The invocation flag is recorded at the provider SDK
boundary, independently of router construction and model names. It proves SDK
invocation, not remote delivery. Reports include the authorization correlation
ID and authorization, result and decision audit statuses.

Authorization must be durably recorded before an SDK invocation. An
authorization-audit failure blocks cloud execution; local results survive.
A result-audit failure after invocation preserves the actual result and marks
the audit failure, without retrying inference. Denial/unavailability audit
failures are also visible and do not erase the local result.

For video, only extracted media artifacts are eligible for cloud review.
Authorized image/audio review can submit uploaded source bytes; the UI and
custody disclosure state that distinction. Local text-only media analysis
does not claim semantic examination of pixels or audio. Evidence hashes do
not establish authenticity, authorship, identity or attribution.

Validation uses synthetic specimens, real generated image bytes and injected
provider fixtures. It covers intake through HTML/JSON/Markdown reports,
local-only and job-reset behavior, approved cloud success, policy refusal,
provider unavailability/setup errors, malformed selection/output, transport
failure, authorization-ledger failure, decision-audit failure and
post-call result-audit failure. The complete regression command is:

    python -m pytest -q

Pytest now discovers both test_*.py and legacy tests_*.py within the canonical
Core/Node paths; vendored duplicate tests remain excluded.

Independent GLM reviewer on port 8001 passed the corrected source. Root review
rejected unsupported findings against fields already present in the source,
and accepted/corrected the canonical-state and SDK custody issues. Static
review does not verify live provider behavior. Tests do not use live Vertex
credentials or send user evidence. No main merge, public deployment, model
change, OS network sandbox or model forward-pass enforcement is claimed.

Provider packaging remains mixed installed Core plus vendored optional
Vertex transport, as documented in apps/node/README.md. Human intent is a
trusted local-caller boolean, not authenticated identity or cryptographic
approval; host/runtime access remains a separate boundary.
