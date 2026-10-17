# Governed isolated OpenClaw report write

Status: narrow real file action, demonstrated October 16, 2026. This integrates the existing OpenClaw v0.3 text-generation endpoint with a fixed Verity file executor. It does not intercept arbitrary OpenClaw tools.

## Boundary

An operator prepares a sanitized copy as input/report.md in a new task workspace. The propose_openclaw_report.py wrapper reads that one file and calls the M1 mini loopback /v0/generate endpoint. The model receives untrusted report text and returns bounded JSON lists. It has no shell or file tool. The inspected OpenClaw source exposes /healthz and /v0/generate; the final runtime log records generate_text allowed and file_write, shell and network denied. Those fields describe the submitted request and profile; they are not host sandbox enforcement.

The wrapper refuses symlinked workspace and input directories and opens the fixed report through no-follow directory and nonblocking descriptors. It requires a bounded regular source with exactly one hard link, so a linked original report or substituted FIFO is refused before the model call. The operator must still sanitize the content before copying it; these file checks cannot prove that a copy is sanitized or stop a process with the same user rights from changing workspace contents. The wrapper renders Markdown and writes staging/proposal.json. The proposal carries source digest, runtime event ID, response digest, activity run ID, exact proposed bytes, fixed target summary.md, and policy snapshot. The staging file is not the governed consequential output. The M1 pilot installation and original reports remain outside the new workspace. The proposal and checkpointed activity record were transferred to the MacBook and imported with explicit M1 provenance.

The MacBook Verity service binds to loopback. Its operator route checks the actual loopback peer, same-origin request, per-process token, exact form fields and current record head. Approval binds the record ID and complete manifest. The only implemented output is summary.md in an explicitly configured directory separate from the workspace and repository. Unknown paths and changed parameters are refused. An approved execution reserves an attempt in SQLite before atomic temp-file replacement, reopens the output to verify its digest against the approved bytes, then records the result. A failed disk check leaves the reservation in doubt. An incomplete reservation is in doubt and cannot be retried automatically. A separate disk check opens the output without following a final symlink, refuses a nonregular file without blocking, and computes the current hash.

This route gives this model call no alternate OpenClaw shell or file tool. It does not confine every process under the same macOS user. An operator with filesystem access, modified wrapper, other agent integration, direct DB manipulation, or compromised host could bypass it. The form token is not authenticated human identity. Hash chains require separately retained checkpoints to detect complete rewriting. A current output digest is not proof of historical authorship. OpenClaw v0 did not intercept and deny a native file-tool call; Verity controlled its own executor.

## Reproduction

Use branch glm/real-openclaw-action-v0.1 at the tested commit. Confirm M1 service health, model inventory and normal memory pressure before loading its existing 7B model. Provide an absolute workspace containing input/report.md, an output directory outside it and a separate SQLite data directory. The reporter contacts only 127.0.0.1:8090 and offers no shell tool.

    PYTHONPATH=.:apps/node python3 tools/propose_openclaw_report.py \
      --workspace /path/to/isolated-agent \
      --db /path/to/isolated-data/report-actions.db \
      --output-dir /path/to/isolated-output \
      --model qwen2.5:7b-instruct

The M1 host Python can run this standard-library reporter, but its expat linkage prevents pip/venv bootstrap. The demonstrated Verity HTTP UI ran in the MacBook isolated test environment. The prior native package was preserved and does not contain this new code. The operator reviews exact bytes at /report-actions/{record_id}, records approval or rejection and executes only after approval. Agents and Governance link the same record and the detail links to /monitor/runs/{activity_run_id}. Export the decision and retain its checkpoint separately. Verify the disk file independently. The UI marks imported M1 activity and its M1 receipt times. A disconnected agent card shows its last reported task when no task remains active, while preserving the full run link and a separate warning for interrupted consequential work.

## Acceptance

Final linked run: OpenClaw event 6481d80e-ade2-4084-8af2-a54257f1c94b, activity dev-20261016-openclaw-acae618955d540fe92790c4c66bb8dc9, governance record tr-7a026a888cb94660ba52469aa276d014. The response digest matched the M1 runtime log. Approval and execution each returned HTTP 303; record state is written. Independent disk SHA-256 matched proposed bytes f56053a23b4cd12f414ddfb4710a9fb4e535fd20da827fb3cd58cc157474dd0b. The M1 independently verified the MacBook export against its retained checkpoint and matched source and runtime hashes.

Separate live acceptance: a rejected record denied execution without changing the output; changed form parameters returned 422; a prohibited path was rejected. Six concurrent executions yielded one 303 and five 409 responses, with one attempt. After service disconnect, an approved record survived restart and executed once. A synthetic report instruction to overwrite the roadmap was omitted from the bounded proposal; that proposal was rejected and the file remained unchanged. Focused tests simulate a crash after reservation, leaving an in-doubt, non-retryable record. This does not establish exactly-once effects for arbitrary external actions.

At a9e434e the MacBook and M2 Pro mini suites each passed 710 tests, two skipped, and one existing Starlette/httpx warning. With the subsequent single-link source guard, the MacBook full suite passed 711 tests, two skipped, and the same warning; the separate M2 suite result follows in the next checkpoint. The M1 full pytest environment is blocked by its host Python expat linkage; its standard-library task and cross-machine verifier ran. Clean-account installation, Developer ID signing and notarization remain pending. See the October 16 session report for evidence paths. Keep activation codes, issuer keys, collector tokens and original private reports out of this workspace and Git.
