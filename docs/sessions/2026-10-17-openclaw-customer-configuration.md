# OpenClaw boundary and customer configuration checkpoint

Source branch: `glm/real-openclaw-action-v0.1`. All demonstrations use disposable profiles and output. The installed pilot was not replaced.

## Actual runtime and boundary

On the M1, OpenClaw v0 answered `/v0/generate` using Ollama `qwen2.5:7b-instruct`. The captured event `35e58f14-efb8-4b0c-ac55-f0e7c8803738` returned a complete text response; its SHA-256 is `0df3e1fa254232cf2c0b651f4cad59aa8ff2bc726eb6499db92af152aa591d98`. The runtime API observed has generation and health routes, not a file-writing tool endpoint. A write prompt and a caller-supplied `tools_allowed=['file_write']` produced text but no file. The latter field is caller supplied metadata, not proof of a capability boundary.

Verity's fixed report broker accepted that exact response as a proposal for `summary.md`; an operator approved it, and the broker wrote and checked the actual file. The disposable Mac native case is `tr-0fe3009c10a74549ae0e5d3b4644f8bf`, with independently checked export and separate checkpoint under `customer-action-native-smoke`. The reproducible runner `tools/openclaw_boundary_acceptance.py` also demonstrates rejected action, wrong approval head, expired approval, restart, replay refusal, stale configuration refusal, actual output digest and independent history verification. Its synthetic time advance is not a real waiting period.

**Enforced:** only the report broker's designated `summary.md` write through its route, bound to exact content, action, policy, agent configuration version and approval head. A pending approval is refused after the configured agent changes. Existing completed evidence retains the original effective configuration.

**Observed or asserted:** OpenClaw's response, runtime event ID, machine link, tool list and model host. The imported event ID was compared to the M1 log manually; the broker does not authenticate the remote event. Endpoint health is reachability, not machine identity or task completion.

**Bypass:** OpenClaw currently runs as the same `swarmtracker` admin user that can directly write disposable M1 files; a direct Python write under that identity succeeded outside Verity. It could also modify same-user records or approach other same-user processes subject to their permissions. A separate least-privileged OpenClaw/executor identity has not been deployed or tested. The remote command approval layer rejected a `sudo -n -u nobody id` probe as disallowed; no alternate escalation was attempted. Therefore the agent is not described as fully governed. The Mac broker's own profile/output files are also same-user accessible. Emergency stop and operator recovery remain existing controls, not a proven OS isolation boundary.

## Configuration implementation

`/configuration` provides immutable per-record versions for machines, model connections, protected resources, intended relationships and agents. The operator can set primary and fallback connection, instruction text, descriptive allowed tools, accessible resources and external sharing (`none` or `explicit_per_job`). Connections accept loopback local endpoints and external HTTPS registrations. External registrations do not contact the endpoint or dispatch work. Credentials are not accepted in this UI or exported. A loopback health observation expires to stale after five minutes. The M1 OpenClaw, MacBook Ollama, and an uncontacted external candidate were entered in a disposable native profile; machine/host links remain explicitly unconfirmed.

Each new governed report proposal saves the effective agent, connection, fallback, resource and reported machine versions/hashes, instructions and digest, tools, sharing, model identity/revision, endpoint and fixed executor in its append-only action manifest. The action page shows that historical snapshot. A consequential edit creates a new version; it does not change older evidence, and the broker refuses to execute a pending action when the current effective configuration differs. The settings describe scope but do not change OpenClaw's actual tools, OS permissions, model internals, or enable fallback dispatch. Existing legacy action records remain readable without a configuration snapshot.

## Tests and evidence

- Linux full suite after the initial configuration and broker changes: 783 passed, 3 skipped. Focused tests after the final snapshot-display change: 24 passed; a final full-suite run is recorded in the daily session report.
- Disposable runner: `session-data/openclaw-boundary-demo-v5/acceptance.json`, history, separate checkpoint and output; `history_verified=true`, `stale_config_blocked=true`, wrong head/expiry/replay blocked.
- Mac native UI: `session-data/configuration-native.jpg` and `session-data/action-native.jpg`; disposable profile on MacBook, with native action export/checkpoint and file hash independently verified.
- GLM focused threat and contract reviews finished with `finish_reason=stop`; useful findings were checked against code. Imagined schema and unsupported enforcement claims were rejected.

A separate OS identity and protection of the output and database are required for a strong non-bypass claim. The installed pilot, GitHub push restriction, and automatic provider shutdown setting were unchanged.
