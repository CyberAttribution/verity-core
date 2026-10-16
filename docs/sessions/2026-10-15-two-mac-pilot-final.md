# SwarmTracker two Mac pilot continuation October 15 2026

Session DEV_101526_SCALEWAY. Report prepared October 15 2026 at 10:11:22 PM EDT. This continues the same rental and development session as the preserved 8:35 PM report, not a second session or an additional compute charge. The earlier termination recommendation was premature relative to the authorized backlog. Work resumed, completed the three requested software milestones, and continued through enrollment and recovery acceptance.

**Software result:** a separate native pilot completed real local analysis, visible activity, governed simulation, export and independent verification on the MacBook and M1 Mac mini. Offline enrollment now includes invitation preparation, tracked issuance, readable persistent activation, runtime diagnostics, expiration/replacement handling and cumulative revocation imports. The final candidate is ad-hoc signed for controlled testing; it is not a notarized public release.

**Resource result:** GLM stopped at 9:57:30 PM EDT with all four GPUs at zero memory use. The guest was still reachable at 10:05:38 PM EDT, and its model container was Exited (0). Provider termination, retained resources, account balance and final billing are **not confirmed**. Manual termination was requested after assessing the remaining backlog; only Mac/file work remained. No Scaleway control plugin, configured CLI or API credentials were available, and the cloud console returned Site Unavailable. Do not treat model shutdown as instance termination.

## Demonstrated milestones

| Milestone | Demonstrated result | Practical limits |
| --- | --- | --- |
| Two Mac workflow | Separate native installations and data profiles, valid activation, OpenClaw or direct Ollama, real inference on a synthetic specimen, visible activity and participant histories, decision preservation, exports and cross-Mac verification | Existing user accounts; no pristine OS or separate-account test |
| Governance and recovery | Approval, rejection, direct bypass denial, altered-action refusal, record-specific approval binding, native restart, failed connection/reconnection, concurrent retries and process-death recovery | Only the fixed harmless local simulator; no external action executor or authenticated operator identity |
| Usable enrollment | Concrete installer ZIP and README, unsent invitation, administrator registry, installation-specific code entry, persistent license/expiry display, model/runtime setup, metadata-only connection test, first analysis and enrollment acknowledgment | Offline local bookkeeping; no forced remote revocation or hardware attestation; signing/notarization pending |

The two-Mac demonstration used one MacBook and one M1 Mac mini. It does not establish the roadmap's broader two-mini, multi-framework delegation demonstration. No additional paid resource was provisioned.

## Machines and preserved installations

The M1 Pro MacBook has 16 GB RAM and macOS 15.7.4. The session installed official Ollama 0.40.5 in its own session directory, with an isolated model store and OLLAMA_NO_CLOUD=1. It used qwen2.5:3b-instruct Q4_K_M, digest 357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b, approximately 1.80 GiB of weights. The runtime is separate from the application; no global application replacement or launch agent was installed.

The M1 Mac mini has 16 GB RAM and macOS 26.1. Its existing Ollama 0.15.6 and OpenClaw v0 service were already running on loopback ports 11434 and 8090. Its existing qwen2.5:7b-instruct Q4_K_M digest is 845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e, with 4,683,087,332 bytes of weights. The original OpenClaw source hash was retained and matched after testing. The existing repository remained at 0189c4fdfae58362b0fdbcfa850bde8101a8d163. Configuration and installed software were preserved; normal test inference entries were appended by the existing runtime to its own log.

Mini tests live under `/Users/swarmtracker/swarmtracker-pilot-tests/2026-10-15`. MacBook work and backups live under `/Users/spike/swarmtracker-session-backups/2026-10-15`. Pilot writable data stayed in explicit isolated profiles. The final pilot's normal default is `~/Library/Application Support/Verity Pilot/data`, separate from the earlier Verity default. The mini's runtime ledger was observed read-only through the explicit integration setting.

The mini's host Python encountered an expat linker error while bootstrapping a verification virtual environment. Its installed Python was not repaired or upgraded. The frozen application ran without that host Python; the standalone Trust Record verifier also ran using standard-library code, without installing dependencies into the existing environment.

## Acceptance results

The form-level acceptance scripts exercised the actual bundled worker owned by the running native app. Native decision/agent screens were navigated and visually checked separately. The final candidate additionally submitted Test saved connection inside its own WKWebView on both Macs, rendered the result, quit and released its owned backend. These are automated native/backend tests, not a claim of a manual Finder/download/quarantine walkthrough.

| Machine | Acceptance batch | Checks | Result | Real analysis time |
| --- | --- | --- | --- | --- |
| MacBook | acceptance | 34 | PASS | 6.72 s |
| MacBook | relaunch-acceptance | 32 | PASS | 3.20 s |
| MacBook | recovery | 16 | PASS | not an inference test |
| MacBook | enrollment-acceptance | 35 | PASS | 6.49 s |
| MacBook | runtime-reconnected-acceptance | 35 | PASS | 6.05 s |
| Mac mini | acceptance | 34 | PASS | 18.00 s |
| Mac mini | relaunch-acceptance | 32 | PASS | 6.95 s |
| Mac mini | recovery | 16 | PASS | not an inference test |
| Mac mini | enrollment-acceptance | 37 | PASS | 16.19 s |
| Mac mini | enrollment-final-acceptance | 35 | PASS | 13.07 s |

The first MacBook analysis took 6.72 seconds; the first mini OpenClaw analysis took 18.00 seconds. Later times benefited from changing load and model warm state; they are observations, not a controlled performance comparison. The final mini successor-license analysis took 13.07 seconds. The final memory-preflight build changed diagnostics/native smoke and administrator tooling; the real analysis/governance journey had passed on the preceding enrollment build with the same analysis/governance implementation. The final build's native connection form and bundled metadata path were exercised on both Macs.

The complete source suite passed **694 tests, 2 skipped, 1 existing Starlette/httpx deprecation warning** on Mac Python 3.14.7 (33.36 seconds) and Linux Python 3.12 (60.70 seconds). Command: `PYTHONPATH=.:apps/node python -m pytest -q`. This is 44 more passing tests than the 8:35 PM checkpoint and 81 more than the historical 613-pass baseline.

Both Macs retained exact approved/rejected decision histories across native restart. Each six-request concurrent execution batch produced one HTTP 200 and five HTTP 409 conflicts. Fresh retries after execution returned 403 and retained one execution entry. Rejection, missing operator intent, stale heads and cross-record approval attempts did not execute. Explicitly supplied altered action parameters were refused with 422.

The process-death regression exited a subprocess after its SQLite update but before commit: the whole action transaction rolled back. A second subprocess exited after commit but before a reply: the durable record remained, and a retry was denied rather than executing twice. The only simulator effect is its committed record, so this does not establish exactly-once behavior for future external side effects.

A real in-flight disconnect test stopped only the session-owned MacBook Ollama server after request activity began. Verity did not accept an interrupted analysis as a completed report, and it retained failed activity. The owned service restarted with the same model store/cloud-disabled setting; a new real analysis and 35-check acceptance batch passed. The mini's existing services were not stopped for this test.

## Exact failures and fixes

1. Setup displayed an Ollama address but analysis always called the legacy OpenClaw endpoint. Added an explicit persisted runtime choice, direct-local Ollama transport, no-proxy/no-redirect loopback checks, local weight metadata validation, bounded 4K context/1,024-token generation, and refusal of empty, truncated or substituted-model replies. Legacy OpenClaw behavior remains available.
2. A missing runtime event could fall back to the latest unrelated ledger event. Removed that fallback. Runtime event IDs remain self-reported metadata; every analysis cache token is independently generated. Reused runtime IDs no longer overwrite previous analysis snapshots. An existing regression assumed the old event-ID URL; it was updated to follow the actual returned intake link and a repeated-ID regression was added.
3. Identical manifests could have identical approval bindings across separate records. New records bind authorization to their record-specific scope; validly rehashed transplanted approvals are rejected. New histories record proposal, authorization, attempt, controlled execution and outcome distinctly. Earlier unscoped exports remain verifiable as historical records with their retained checkpoints.
4. External development collection alone did not make the normal recipient's analysis visible. Added product-owned request/heartbeat/completion activity without prompts, model responses or tool arguments. Failure to open activity storage prevents inference; failure to retain completion does not claim a completed report. Thread-start and invalid-runtime-response failures are tested. A failed-completion test initially inspected completed activities instead of pending work; it now checks the actual interrupted pending state.
5. Initial setup errors were generic JSON and activation expiry was a Unix number. Added actionable HTML errors, persistent license ID/UTC expiry, exact installed model names, separate service/inventory/model states, and a bounded metadata-only connection test. The actual saved configuration survived relaunch; a GLM suggestion that startup discarded it was rejected against the worker implementation and both native relaunch tests.
6. The proposed append-only JSONL enrollment registry had unsafe short-write/torn-tail assumptions. Implemented SQLite reservation before code export, explicit pending reconciliation, deterministic Ed25519 re-export from retained claims/key, private descriptor reads, partial-file preservation, no ID reuse and no replacement forks. Invitation preparation likewise records prepared/exported states and can repair a partial draft. Cross-file/database atomicity is not claimed.
7. Direct LAN transfer between the two Macs failed. A temporary artifact-only restricted SSH transfer preserved package hashes without changing either firewall. Its server grant and the temporary mini private key were removed afterward. No public inference endpoint was opened.
8. The mini's first 7B test began with normal pressure and no swap, but later multiple QA app windows plus desktop work produced warning pressure and about 411 MiB swap. Closing the obsolete test app and unloading the test-loaded model restored normal pressure. The final connection form now shows inventory-reported model weight size and this Mac's pressure, warns before loading under pressure, and says that normal pressure is not a fit guarantee. A private tunnel may point at a different runtime host. The final MacBook native form visibly exercised warning guidance; the mini form displayed normal pressure and 4.36 GiB weights.

The mini's observed loaded model allocation was 4,924,207,104 bytes (about 4.59 GiB) at 4K context; the estimated KV cache was approximately 224 MiB. A 16 GB desktop running other applications can have insufficient comfortable headroom even when the weights alone fit. No extra mini model was downloaded. The existing 7B model was unloaded after testing, and its original services remained healthy.

## Offline enrollment and invitation

The administrator-side registry is owner-only SQLite, outside source and packages. Four licenses are tracked: the MacBook license and three mini licenses linked as a replacement chain. The mini's current installation is `install-d663d91344e647b48862f360eecb4fbf`, with successor license `pilot-mini-enrolled-20261015-3`. The MacBook installation is `install-f1592720fbf44962bf1154f95ae23d65`. Both current licenses have operator-supplied first-analysis acknowledgments backed by independently verified exports/checkpoints. This association does not prove machine identity or evidence truth.

The mini passed 14 lifecycle checks: replacement activation; old code still valid before revocation import; revision 1 revoking the original; revision 2 cumulatively revoking original and replacement; denied reactivation of revoked codes; a new successor restoring activation; rejection of revision rollback and non-cumulative signed lists; and expired/wrong-installation codes preserving the current state. The final license, runtime/model settings and imported list survived native relaunch. Initial wrong-installation and expired signed codes were also rejected before any activation.

An unsent sample invitation references the exact final package filename and SHA-256. Its tracking ID grants no authority. First launch produces the installation ID; the administrator then issues a separate signed code, normally seven days (CLI bounds 1–30 days). Raw codes and the private issuer key are absent from the registry, package, report and source backups. The invitation is **not sent**.

Replacement does not remotely invalidate a prior code. A signed cumulative revocation list takes effect only after the installation imports it; the administrator registry records list issuance, not proof of import. There is no forced delivery, online enrollment server, hardware binding or instant offline revocation. Expiry/rollback checks depend on local state and the system clock; copying/removing state is outside the security claim. The pilot revocation schema caps a list at 100 license IDs. Activation does not authorize per-job cloud evidence transfer.

Reproduction and administrator instructions: `docs/pilot-enrollment.md`, `docs/pilot-installation.md`, `tools/pilot_enrollment.py`, `tools/pilot_entitlement.py`, `tools/packaged_acceptance.py`, `tools/packaged_recovery.py` and `tools/pilot_lifecycle_acceptance.py`.

## Source and installer

Repository https://github.com/CyberAttribution/verity-core . Branch `glm/live-monitoring-installer-v0.1`. Accepted code head `d353ad91346eb464a418706dcd533555ff42bfa5` was pushed and cloned independently into the mini's separate `source-final-git` directory. Original checkouts were preserved and main was not merged.

- `5321e54edf447c3e6d07fbebca3f719eaaf027e8 Complete packaged local analysis and scoped governed export workflow`
- `341cf80caf50d69ba9a07bcb35395bfc447ab12e Verify report snapshots survive reused runtime event IDs`
- `9b68f88af3eaf29f7420d72e3f9a3c0f661b9c7a Demonstrate two-Mac pilot and transactional crash/retry recovery`
- `f0256d13889c4b9f22388602bdb219ea69a86601 Complete tracked offline enrollment and actionable pilot setup`
- `d353ad91346eb464a418706dcd533555ff42bfa5 Prepare unsent invitations and expose memory preflight from two-Mac findings`

Final candidate `Verity-Pilot-AppleSilicon-macOS15.zip`: **29,204,823 bytes**, SHA-256 `16701bba6d325ac45ccb856e1faf5b0c9095ed964d6257ad0cdee74fc60e6c4d`. ZIP CRC and absence of private issuer/collector/activation artifacts were checked. The archive contains the relocatable `Verity-Pilot.app` and recipient README. The app's ad-hoc signature verified on both Macs. Build source was clean at `d353ad91346eb464a418706dcd533555ff42bfa5`, source-files SHA-256 `29baeb412f43c800d0211de10b1bb4a15cb15c34a78ed8451e7005eb3c355ca8`, bundled Python 3.14.7.

Developer ID/notarization, quarantine/Gatekeeper acceptance, a pristine separate-account install, Intel/older macOS support and wider pilot feedback remain release work. Optional media binaries are not bundled. The new default data directory avoids replacing existing Verity data; tested upgrades reused only explicitly isolated pilot profiles.

## GLM contribution

The pinned `canada-quant/GLM-5.3-Flash-W4A16-MTP` deployment from the earlier report was restored after the continued backlog was authorized. This continuation completed **13** real implementation/review requests, in addition to the earlier seven real requests. Across the session, 21 development workflows were exported and checkpoint-verified: 20 actual completions (the original first completion remains incomplete at its token limit) and one preflight failure that made no model call.

GLM proposed direct-local runtime support, enrollment administration, invitation preparation and memory-preflight changes, and reviewed governance, recovery, setup, privacy and failure handling. Its output was evaluated against actual contracts and tests. Several suggestions were rejected: generic OpenClaw `id` is in fact the request UUID written by the inspected existing runtime; startup already reapplies saved settings; normal pressure is not proof of memory fit; and a char-pointer call to the wrong sysctl API was replaced by a fixed bounded command. It was useful implementation/review assistance, not an autonomous coding executor or a human security certification.

| Continuation request | Outcome | Prompt / completion tokens | Total client time |
| --- | --- | --- | --- |
| enrollment-implementation | completed / stop | 4692 / 3009 | 19.45 s |
| enrollment-recovery-final-review | completed / stop | 5486 / 2707 | 16.25 s |
| enrollment-registry-review | completed / stop | 5099 / 5594 | 35.67 s |
| final-scope-review | completed / stop | 12278 / 3719 | 23.42 s |
| glm-direct-ollama | completed / stop | 5536 / 3024 | 15.39 s |
| governance-local-review | completed / stop | 11941 / 1948 | 25.14 s |
| governance-revised-review | completed / stop | 12163 / 2971 | 18.08 s |
| invitation-enrollment-review | completed / stop | 5202 / 3357 | 20.92 s |
| invitation-implementation | completed / stop | 5712 / 1933 | 10.30 s |
| memory-preflight-final-review | completed / stop | 7000 / 1646 | 10.19 s |
| memory-preflight-implementation | completed / stop | 1896 / 2120 | 13.32 s |
| restart-binding-review | completed / stop | 12372 / 3726 | 23.79 s |
| setup-activity-review | completed / stop | 5723 / 6309 | 38.06 s |

Inputs, sanitized outputs, timings and telemetry are retained in the session backups. The exact model revision, Docker digest, TP4/MTP configuration and reproduction script remain those in the earlier preserved report. No isolated quantization performance comparison, concurrency saturation result or complete agent-framework monitoring claim is made.

## Independent verification anchors

The latest MacBook approved decision and native activity were copied to and verified on the mini using the source-only standard-library verifier. The latest mini successor-license approved/rejected decisions and native activity were verified on the MacBook. All 21 development workflows also passed verification against their separately retained checkpoints.

| Export | Record ID | Entries | Retained head SHA-256 |
| --- | --- | --- | --- |
| mac approved | `tr-8f874786802c424e960a7ccc094868c3` | 7 | `1cf95cd3de263decfca76691a9401201b83088bfae6900e6746913f90c8bd6d1` |
| mac rejected | `tr-baa688bddfa4436782e75698d4f8e2f9` | 6 | `e66bf88b4181d0dc9cf2fc4eb7922c71f19cdfd2e171cf70e2987f418770c475` |
| mini approved | `tr-96791da4a41c4667a317bf407d2271a1` | 7 | `da380da04ae585b2ea04dc04696589151b55c31e7e1a2bcd968d522b3f6920d3` |
| mini rejected | `tr-2d09375b0e264722a8120bba1ddf9058` | 6 | `cec1d5ec80597833739e8c6ef1137cdc0d92dfa75c13611c10e1a680493f0ce1` |

Integrity is relative to these checkpoints. Verification does not establish evidence truth, authenticated actors, complete capture, immutable storage or resistance to a full rewrite without a retained external anchor. The only controlled action demonstrated is the fixed local simulation; no sender was blocked and no external action was taken.

## Spending and resource status

Scaleway instance `scw-elastic-carson`, ID `116647f4-c9cb-443d-af16-e634dd629ee2`, H100-SXM-4-80G, fr-par-2, public IPv4 51.159.155.246. Guest boot 7:40:46 PM EDT. The public product API at https://api.scaleway.com/instance/v1/zones/fr-par-2/products/servers reported EUR 12.771/hour for this exact type during the continuation. Account-specific discounts, taxes, storage/IP charges, pre-boot billing, credits and final invoice remain unavailable.

Guest-boot arithmetic was **EUR 29.10 when GLM stopped at 9:57:30 PM EDT**. If the instance remains allocated through this report's preparation time, the same arithmetic is **EUR 32.06**, before those unverified charges. Neither figure is a final bill. The earlier 8:35 PM estimate is superseded for this same rental and must not be added again. Historical Lambda USD totals are unchanged. The EUR 70 ceiling was not deliberately extended, and no additional paid instance/resource was created.

At 10:05:38 PM EDT the guest was reachable, the model container showed Exited (0), and all four GPUs showed 0 MiB/0 percent. The local SSH tunnel and development collector were stopped after exports. This does not terminate the provider instance. Provider-side termination and retained volume/IP status require confirmation; manual termination was requested after the complete backlog assessment. Remaining work at that point was document QA, verification and backups on the Macs, with no useful GPU task left.

## Backups and handoff

MacBook retains a verified complete Git bundle, the accepted source archive, final installer/README, build/test logs, twenty saved completion results/inputs, 21 development exports/checkpoints, both Macs' acceptance/recovery exports and the administrator metadata snapshot. Raw activation codes, collector tokens, installation activation-state files and the private issuer key are excluded from source/report/package backups. The private administrator key and code artifacts remain in their separate owner-only administrator directory.

The mini independently holds the final installer with the matching hash, a separate clone at the accepted code head, its own acceptance evidence and independently verified MacBook exports. Its original OpenClaw/Verity installation is preserved. Final report/planning-document copies and final documentation-only commit/bundle references are recorded in the saved handoff manifest after this report is written. No RAID volume was used or claimed.

Existing planning-file identities are retained: tracker `libfile_85b982b8abe88191b000c9b691d7165d` and roadmap `libfile_39dec77e74f0819180289a4e789b69cd`. The earlier report `SwarmTracker_DEV_2026-10-15_Scaleway_4xH100_2035_EDT.md` remains unchanged. This new report is saved in `/SwarmTracker_Lab/Session_Reports/`; the installer and unsent invitation are saved in `/SwarmTracker_Lab/Pilot_Packages/`.
