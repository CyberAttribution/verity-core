# October 24 rolling implementation checkpoint

Implementation and focused verification only; broader native matrix, final full regression and synchronization remain pending.

- Explicit additional local Ollama selection stays bound through report generation, approval and execution. Existing configuration transaction serializes edits against a started broker file effect.
- Internal reduced profile creates a new child record without rewriting historical hashes. Offline child integrity is separate from optional supplied-parent correspondence. Raw source, response, instructions and context are omitted structurally; free-form qualifications and corrections require operator review and can still contain private data. This is provisional, not a public compatibility commitment or cryptographic selective proof.
- Ollama residency is provisional and connection-specific. Default retention is 300 seconds; explicit unload is zero. Invalid or non-Ollama options are refused. Short connect timeout no longer truncates the separate bounded inference deadline.
- Real M2-to-M1 managed SSH request and pause denial passed on preceding source 38daf64. Immediate reconnect failed, consistent with the plain pre-bind probe rejecting TIME_WAIT. Probe now permits address reuse while refusing live listeners; actual paired reconnect reacceptance remains pending. SSH authentication, model identity observations and Verity mediation remain distinct; no OS containment established.

Focused checks: 28 transport/HTTP/profile/framework checks passed through permitted socket access; 16 residency/profile/framework checks passed afterward (overlapping sets, not additive suite coverage). Delayed headers beyond five seconds and finite trickle interruption were exercised. Full regression for this checkpoint is deferred.

Private session evidence retains two initial measurement failures caused by the five-second socket timeout and two new completed requests after repair. Both successful requests included unloading: approximately 7.73 and 8.24 seconds wall time, with approximately 5.73 and 5.21 seconds model load. Memory/swap recovery assessment and native acceptance remain pending; allocated swap alone is not active swapping. Model output quality failures are retained separately from transport success.

## Subsequent transport and timeout verification

On the real M2→M1 path at 50edf53, start, real request, owned stop, immediate reconnect and a new real request passed. After stop, lsof showed no listener, netstat retained TIME_WAIT sockets, plain bind failed with errno 48 and SO_REUSEADDR bind succeeded. Exact retained completed-request recovery did not increase dispatch count. A controlled lost acknowledgement preserved its raw runtime output and reserved request; recovery refused without redispatch. These tests do not establish arbitrary exactly-once external effects.

Actual paired pause/stale-observation/lost-forward/changed-version/transport-removal refusals passed with zero inference calls. Peer-trust verification evidence is tracked separately; do not infer its completion from these results. Broader native GUI acceptance and independent receipt/backup verification remain pending.

Further implementation retains owned SSH handles and registry entries when exit cannot be confirmed, disables dispatch on cancelled transport, returns unknown on control-check errors, and provides readable stop refusal. Browser status and request evidence distinguish SSH peer trust, configured/returned model identity, mediation and containment limits. These later changes require final native reacceptance.

HTTP establishment has a short bound and no automatic retry; its elapsed time now consumes the total request budget. Six focused tests separately exercise establishment failure, budget exhaustion during connect, legitimate delayed headers beyond five seconds, trickle interruption, redirects and oversized responses.

The bounded MacBook experiment completed two new requests after the fix; output throughput was approximately 19 and 10 tokens/second. Active system-wide swapping persisted after unload, including a final sampled interval near 25 MiB/s. This is not all attributable to Ollama and is distinct from allocated swap. Further stress was stopped; use the Mini for development inference. Preserve the installed MacBook model and explicit unload setting for optional use. Native acceptance and restart checks remain pending.

The copied-source failure is retained privately as a labeled B18/B25 evaluation case, including exact input/output/model/configuration and expected one-source result. Existing source-dependence/disagreement qualifications now appear in profile review. No automatic source-independence verification is claimed.
