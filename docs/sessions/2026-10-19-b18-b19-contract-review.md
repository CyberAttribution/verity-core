# B18 and B19 evidence and context contract review

Status: source-grounded architecture checkpoint, October 19, 2026. This specifies the next bounded integration; it is not a completed evidence-verification or managed-context feature.

## Existing components to extend

| Component | Existing behavior | Extension boundary |
| --- | --- | --- |
| `verity_core/trust/record.py` | Standard-library JSON lifecycle entries, canonical serialization and hash chain. Existing entry kinds include evidence, analysis, validation, approval and outcome. | Put the versioned check/context identity inside hashed entry payloads. Subject and refs alone are not hash protected. Do not invent another chain. |
| `verity_core/trust/decision.py` | Evidence IDs, exact content hashes, participant-linked findings, conflict flag and approval binding. | Preserve claim/check links and unresolved disagreement; checking evidence never grants approval. |
| `apps/node/swarmtracker_node_ui/company_work.py` | Source claim, governed interpretation, explicit human decision, assigned task and observed Git object. Verified result is still empty. | Link an evidence check to the interpretation; do not promote it to a company decision or task completion. |
| `verity_core/artifacts/fuse.py` and `schemas.py` | Atlas entities/relationships and per-artifact provenance; deterministic correlation is separate from model narrative. | Add approved context references beside evidence provenance. Existing graph relationships are not approved organization facts or trusted policy. |
| `canonical_case.py` | Report/version history and reserved Atlas references. | Reference exact context/check versions while preserving historical reports. |
| Customer config and local inference | Immutable effective configuration snapshots; bounded routed request/cap checks and explicit adapter limitations. | Bind future checker/context identity to the request snapshot; extend strict file-action export validation deliberately. |

## Shared identity and evidence fields

Use stable claim, source snapshot, check, workflow, agent and task IDs. Each source snapshot needs origin/reference, retrieval time, source revision when known, UTF-8 content hash, content location and source-quality/freshness limitations. Preserve the actual bounded snapshot needed for reconstruction. A URL or model-supplied citation alone is insufficient.

A supporting passage identifies its source snapshot and exact character span plus quoted text. Verify the snapshot hash and span deterministically. This proves that the passage occurs in the checked snapshot; it does not establish that the passage is true, current or sufficient to support the interpretation.

A versioned `evidence_check/v0.1` payload should record claim text/hash, source IDs, passages, method and method version, checker model/revision/configuration when used, input/output hashes, timestamps, attempts, latency, token usage, billing source and unresolved disagreement. Initial outcomes: supported, contradicted, insufficient, conflicting. “Unsupported” maps to insufficient rather than introducing a false implication. Contradicted requires checked evidence that explicitly disagrees, not mere absence.

Keep deterministic checks for hashes, required fields, quotations and calculations separate from model judgment about meaning. Model completion, syntactically valid JSON and valid passage spans are separate checks. Never silently convert a malformed or truncated result to supported. Present supported by checked evidence rather than guaranteed true.

## Bounded checking and reconstruction

Pilot one small research workflow with independently labeled supported, contradicted, insufficient and conflicting cases, then a source-instruction injection case. Keep labels out of the prompt. Save raw responses before parsing, including on failure. One bounded follow-up may repair a demonstrated interface failure; never loop until agreement. Record first-attempt failure separately from any later result. Use a fixed request/token/deadline allowance and expose actual usage; local counts do not constitute a dollar cap.

Append attempts and revisions rather than overwriting them. Preserve disagreement and send unresolved cases for review. Export the claim, source snapshots, check payload and a separately retained Trust Record checkpoint. A separate verifier must reconstruct hashes, passage spans, link consistency and unresolved outcomes. Chain reconstruction is integrity evidence, not independent semantic agreement or proof of truth. Missed errors and false alarms must be reported even on a tiny calibration; no general zero-error claim follows.

## Approved Atlas organizational context

Start from a small operator-supplied profile; no actual organization profile has been approved in this session. Each entry needs entity/relationship identity, fact/assumption/proposal distinction, provenance, checked time, immutable version/hash, approval status and supersession history. Keep proposed updates outside the approved snapshot. Acceptance/rejection must be explicit and recorded; rejection cannot change the active approved version.

Analysis must record the exact approved profile version and hash in a hashed payload. Missing, stale and conflicting context remain visible. Retrieved documents are evidence and cannot become trusted policy or approved facts automatically. Existing entity labels, model inferences and editable Markdown do not confer authority. A fixture vendor/payment-detail mismatch should request review without executing payment.

## Next implementation and acceptance

First implement a portable validated evidence-check envelope over the existing Trust Record, with a standalone reconstruction check. Then expose check inspection/export within the current analysis flow. Add the small approved context snapshot after that contract is exercised. Do not add a checker management console, second graph service, broad connector or autonomous knowledge-maintenance worker.

B02 OS isolation, live B03 activation, a separately authorized B05 external route, and the original B08 Docker decision remain independent prerequisites. This design neither claims those gates passed nor changes their authorization requirements.

## Portable foundation checkpoint

Implemented the strict envelope and Trust Record export/reconstruction in `verity_core/trust/evidence_check.py`. This is a library foundation, without analysis UI, model invocation controller, approved context store or authenticated approval gate. Recorded request limits do not enforce invocation budgets. A context reference records an assertion and does not authenticate approval.

Five synthetic GPU cases were run with expected labels withheld. The first run failed strict JSON parsing in all five cases; that harness did not retain raw failed responses, so those outputs cannot be reconstructed. A separately recorded format diagnostic preceded a bounded five-case second run that saved raw responses before parsing. All five second-run outcome labels matched the fixture labels, but only four met the citation contract. The contradicted case used `text` instead of `passage` and was rejected without normalization. Second-run usage was 324 completion tokens and 10.2954 seconds across five requests. These fixtures establish no general semantic accuracy or factual-truth guarantee. Four accepted fixture bundles retain separate checkpoints; rejected raw output remains private evidence.
