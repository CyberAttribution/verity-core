# SwarmTracker Node UI v0.4.0

Local web interface for the SwarmTracker Node prototype (M1 Mac mini).
Additive only — no changes to `openclaw_v0_v03_working.py` or the ledger.

## v0.4.0 — governed cloud provider = Vertex AI (ADC, no API key)

Governed multimodal escalation now uses **Vertex AI** via Application
Default Credentials instead of the AI Studio API key. Default model
**Gemini 3.6 Flash**, fallback **Gemini 3.5 Flash Lite** (both
env-overridable; optional Flash Cyber slot). A pluggable provider seam
(`providers.py` + `routers/vertex.py`) keeps Media Evidence unchanged:
Local (default) → Vertex AI → future providers. No credential appears in
any URL, log, export, or exception (sanitized + regression-tested). The
Canonical Case and report record provider, project, region, model used,
inference latency, provider status, and attempt history. Setup:
`VERTEX_SETUP.md`. Env: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`,
optional `VERTEX_MODEL_PRIMARY/_FALLBACK/_CYBER`. Installer adds
`google-auth`. Local Only remains the default; nothing escalates
implicitly.

---

## v0.3.0 — Deepfake Defender media parity (YC demo release)

Feature parity with the public Deepfake Defender v1.0 Media Evidence
pipeline, running entirely on this node (vendored core upgraded to
verity-core **0.5.0** content):

- **Local media upload** (`/media`): 12 formats (mp4/mov/avi/mkv/webm,
  mp3/wav/m4a, jpg/jpeg/png/webp), magic-byte content verification,
  per-family size caps. `media_runner.py` mirrors the reference
  `media_analysis.py` sequence synchronously: sha256 → ffprobe → ExifTool
  → frames + audio (ffmpeg) → `media_evidence_package/v1` → graded
  assessment via the Keel-locked local router → Canonical Case → cleanup
  (success AND failure; hashes/evidence/report retained, media deleted).
- **Graded, bounded assessment**: six-value conclusion (never
  "real/fake"), confidence-by-domain, anti-fabrication (visual/audio
  findings stripped unless that media reached the model), deterministic
  fallback when the model is unavailable, `package.validate()` fail-safe
  (inconsistent evidence ⇒ report withheld), honest "signal-level review
  not performed by this runtime" on the text-only local model.
- **YouTube URL ingestion** (`/media/retrieve`): strict validation +
  canonical URL, bounded anonymous `default→tv→mweb` retrieval (yt-dlp),
  categorized fail-soft with confidence-effect statements. Platform
  metadata is labeled retrieval-generated.
- **Keel policy v0.2.0** (`keel_policy.json`): additive `media_evidence`
  section — local tool allowlist, temp-dir confinement, mandatory
  cleanup, and governed egress scoped to media retrieval only;
  `network_allowed` stays `false`. Fail-closed; four new self-test rows.
- **Media governance ledger** (`keel_events.py`):
  `logs/keel_media_events.jsonl` records every pipeline stage +
  retrieval authorization/denial. Counts on `/governance` + `/healthz`.
- **Canonical Case persistence** (`canonical_case.py`, store schema v2):
  immutable `DFD-` id in SQLite, surfaced as Atlas graph nodes
  (case → assesses → media-hash) and Timeline entries; export/import.
- **Guaranteed demo case** (`/media/demo`): ffmpeg-synthesized labeled
  specimen through the complete real pipeline; zero external deps.
- **Verification gate**: `python -m swarmtracker_node_ui.verify_media`
  (all three families, full chain, throwaway DB) — run before recording.

System tools (Homebrew): `brew install ffmpeg exiftool yt-dlp`.
Python deps add: `httpx` (verification gate).

### v0.3.1 — optional Keel-governed multimodal escalation (Gemini)

The text-only local runtime performs deterministic extraction but no
semantic visual/audio reading. v0.3.1 adds an OPTIONAL, per-job,
operator-authorized escalation: a hybrid router keeps orchestration and
fusion on the local model and sends ONLY the extracted artifacts
(sampled frames · mono audio track · image) to Gemini for multimodal
interpretation. The source file, evidence package, Canonical Case,
Atlas, and both ledgers never leave the node. The report carries an
**Analysis provenance** panel and per-section badges separating local
findings from authorized-Gemini findings; the media governance ledger
records the authorization (with the sent artifacts' hashes) and the
outcome. Policy: `keel_policy.json` v0.3.0
`governed_egress.multimodal_escalation` (fail-closed; provider/kind
scoped; `default_mode: local_only`; two new self-test rows). **Local
Only remains the default** — nothing escalates without the checkbox,
and without `GEMINI_API_KEY` in the environment the option is shown
disabled with an honest note. Failure degrades honestly: findings from
an unexamined medium are stripped (anti-fabrication) and the escalation
failure is recorded.

---

v0.1.1 adds product modes and governance visibility: Profile Mode +
Analysis Mode + mode-specific Event Category selectors on Analyze, a Keel
Trace panel and profile-specific assessment fields on Report, and
Providers / Product Modes / Public Sector tiers panels on Settings.
Only Local Only executes; other analysis modes are selectable roadmap
options with an honest notice, and no cloud calls are made.

v0.1.2 adds Keel governance health: a Governance page with an overall
prototype governance score, Attention Items, twelve searchable/filterable
control-category scorecards (status, confidence, capability, flags, next
hardening step, ledger source fields), a recent-events summary — plus live
governance indicators under the Profile Mode and Analysis Mode selectors.
Scores combine structural facts about this build with evidence from recent
ledger events. Control health, not certification; no compliance is claimed.

v0.1.3 hardens Deployment Policy Locking at the Node UI / Keel layer:
`keel_policy.json` locks the allowed policies, `keel_policy.py` loads it
fail-closed (missing/malformed/over-permissive files collapse to
read_only_analysis defaults), the analyze flow rejects unknown policies
before any runtime call, `openclaw_client` always submits the locked
policy plus policy facts under `meta`, and `/governance/self-test` proves
it. The scorecard moves red → yellow honestly: Node UI-side policy
enforcement is active; runtime-level enforcement is the next hardening
step, and direct calls to OpenClaw may still bypass Node UI validation.

v0.1.4 is a demo-UX + evidence-intake upgrade on the same governed path:
visible Profile Mode cards (now five profiles incl. Legal / Regulated) and
Analysis Mode cards with honest roadmap notices, a grouped cyber-aware
Evidence Type selector (Communication / Security Logs / Business Workflow /
Advanced), a local synthetic Sample Evidence Library (`samples/`, all
labeled "SYNTHETIC DEMO SAMPLE — not real customer data"), safe local
text-file upload (client-side read, .txt/.log/.json/.csv/.eml, ≤512 KB,
never persisted), Security Log and Legal / Regulated report panels, and
Report rows for Executed Mode / Evidence Type / Sample Source / Uploaded
File. Execution remains Local Only under the locked Keel policy.

v0.1.5 (a2) adds Multi-Artifact Fusion powered by the shared Verity Core
artifacts layer (vendored as `verity_core_staging/`, staged for merge into
the private verity-core repo): stage a batch of up to 6 artifacts on
Analyze, run one fused assessment — two-stage pipeline (per-artifact
analysis → deterministic correlation → model fusion), each stage one
governed ledger event under the locked policy. The fused report shows the
Keel Pipeline Trace (stage → event ID), corroboration/contradictions,
bounded attribution hypotheses (campaign types, never named actors),
within-submission campaign indicators, missing evidence, limitations, and
the full shared-schema JSON. Atlas visualization of the extracted
entity/relationship graph arrives in a3.

v0.1.5 (a3) adds **Atlas** — a dependency-free SVG evidence graph
(`/atlas` page + inline panel on every fused report) rendering the
entities/relationships Verity Core extracts: artifacts, senders, orgs,
domains, IPs, emails, URLs, and dashed campaign clusters, with hover
provenance. Fused reports are session-local (max 10, cleared on restart —
persistence is honestly Origin's job, and the Origin preview panel on
/atlas says exactly what the system of record will add). Also adds the
**Emerging Technology** profile (6th): vendor security claims, PQC/HNDL
concerns, crypto migration — claim-triage prompt fields
(plausibility/verification questions/limitations), report panel, sample
`pqc_vendor_claim.txt`, and honest "no cryptographic verification" bounds.

**Deploy note (v0.1.5+):** copy BOTH folders to the project root —
`swarmtracker_node_ui/` and `verity_core_staging/` must sit side by side
in `~/openclaw-v0/`. `/healthz` reports which core source is active.

## Files

```
swarmtracker_node_ui/
├── __init__.py
├── config.py            # env-overridable settings; loopback-only guard
├── app.py               # FastAPI app (dashboard, analyze, report, ledger, settings)
├── openclaw_client.py   # POST /v0/generate + healthz + Ollama check
├── ledger.py            # read-only JSONL reader
├── analysis.py          # prompt builder + defensive JSON parser
├── governance.py        # Keel control-health scoring from ledger evidence
├── keel_policy.json     # locked Keel policy (v0.1.3)
├── keel_policy.py       # fail-closed loader/validator + self-test
├── samples/             # synthetic demo artifacts + index.json (v0.1.4)
├── templates/           # base, dashboard, analyze, report, ledger, settings
└── static/style.css     # dark appliance theme
```

## Install (on the M1)

Copy this folder into the project root so it sits at
`~/openclaw-v0/swarmtracker_node_ui/`, then:

```bash
cd ~/openclaw-v0
source .venv/bin/activate
pip install fastapi "uvicorn[standard]" jinja2 requests python-multipart
```

(`requests` is almost certainly already present; `python-multipart` is
required by FastAPI for the analyze form.)

## Run

```bash
cd ~/openclaw-v0
source .venv/bin/activate
uvicorn swarmtracker_node_ui.app:app --host 127.0.0.1 --port 8077
```

Open http://127.0.0.1:8077

Prerequisites running: Ollama (`:11434`) and OpenClaw v0.3 (`:8090`).

## Smoke tests

```bash
# 1. Backends up
curl -s http://127.0.0.1:8090/healthz | python3 -m json.tool
curl -s http://127.0.0.1:11434/api/tags | python3 -c 'import json,sys;print([m["name"] for m in json.load(sys.stdin)["models"]])'

# 2. Node UI up (aggregated status)
curl -s http://127.0.0.1:8077/healthz | python3 -m json.tool
# expect: runtime_online true, model_online true, ledger_events > 0

# 3. Pages render
curl -s -o /dev/null -w "dashboard %{http_code}\n" http://127.0.0.1:8077/
curl -s -o /dev/null -w "analyze   %{http_code}\n" http://127.0.0.1:8077/analyze
curl -s -o /dev/null -w "ledger    %{http_code}\n" http://127.0.0.1:8077/ledger
curl -s -o /dev/null -w "settings  %{http_code}\n" http://127.0.0.1:8077/settings

# 4. End-to-end analysis (writes a real ledger event; takes 10-60s)
curl -s -X POST http://127.0.0.1:8077/analyze \
  -d profile_mode=smb -d analysis_mode=local_only \
  -d event_category=suspicious_email -d input_type=email_message \
  --data-urlencode "evidence=URGENT: Your mailbox is full. Click http://mail-storage-fix.example-verify.top to keep receiving email. Act within 2 hours." \
  -d claimed_org=IT \
  | grep -o 'Keel Trace' && tail -n 1 logs/openclaw_events.jsonl | python3 -m json.tool | head -20

# 5a. v0.1.5 a3 surfaces
curl -s http://127.0.0.1:8077/atlas | grep -c "Origin — System of Record"   # >=1
curl -s http://127.0.0.1:8077/analyze | grep -c "Emerging Technology"       # >=1
curl -s http://127.0.0.1:8077/api/samples/pqc_vendor_claim | grep -c SYNTHETIC  # 1
# after a fused run, /atlas shows the graph:
curl -s http://127.0.0.1:8077/atlas | grep -cE "renderAtlas|Session Analyses"

# 5. v0.1.5 multi-artifact fusion
curl -s http://127.0.0.1:8077/healthz | python3 -m json.tool   # version 0.1.5, verity_core source
curl -s http://127.0.0.1:8077/analyze | grep -c "Multi-Artifact Fusion"   # >=1
# fused batch of 2 (writes 3 real ledger events; takes ~1-3 min on the M1):
curl -s -X POST http://127.0.0.1:8077/analyze/fused \
  --data-urlencode 'artifacts_json=[{"label":"Email A","evidence_type":"email_message","event_category":"vendor_payment_request","text":"URGENT: update remittance via http://pay.suspicious-example.top/inv","claimed_org":"Apex"},{"label":"DNS log","evidence_type":"dns_log","event_category":"url_domain","text":"query: pay.suspicious-example.top IN A"}]' \
  -d profile_mode=msp \
  | grep -oE "Keel Pipeline Trace|Attribution Hypotheses|Campaign Indicators|CAMP-[A-F0-9]{8}"
# tampered policy still rejected before any runtime call:
curl -s -X POST http://127.0.0.1:8077/analyze/fused -d policy=admin_override \
  --data-urlencode 'artifacts_json=[{"text":"x"}]' | grep -o "No analysis was run"

# 6. v0.1.4 demo UX + evidence intake
curl -s http://127.0.0.1:8077/healthz | python3 -m json.tool   # version 0.1.4, keel_policy_lock active
curl -s http://127.0.0.1:8077/analyze | grep -c "Legal / Regulated"       # >=1
curl -s http://127.0.0.1:8077/analyze | grep -c "Authentication log"      # >=1
curl -s http://127.0.0.1:8077/analyze | grep -c "Firewall / proxy log"    # >=1
curl -s http://127.0.0.1:8077/analyze | grep -c "Endpoint / EDR alert"    # >=1
curl -s http://127.0.0.1:8077/analyze | grep -c "Load Sample Evidence"    # >=1
curl -s http://127.0.0.1:8077/api/samples/public_sector_ach_change | python3 -m json.tool | head -8
curl -s http://127.0.0.1:8077/governance/self-test | python3 -m json.tool # all_passed true, runtime pending

# combined profile + roadmap mode + security-log evidence (10-60s):
curl -s -X POST http://127.0.0.1:8077/analyze \
  -d profile_mode=public_sector -d analysis_mode=cloud_assisted \
  -d event_category=payment_ach_change -d evidence_type=authentication_log \
  --data-urlencode "evidence=Failed login burst for user finance.admin from unfamiliar IP followed by password reset request." \
  | grep -oE "Keel Trace|Executed Mode|Security Log Assessment|Roadmap analysis mode selected"

# upload path is client-side (FileReader); server-side equivalent:
curl -s -X POST http://127.0.0.1:8077/analyze -d evidence_type=authentication_log \
  -d uploaded_filename=test_burst.log --data-urlencode "evidence=$(head -c 2000 swarmtracker_node_ui/samples/auth_failed_login_burst.log)" \
  | grep -o "test_burst.log (local, not persisted)"

# 6. v0.1.3 Keel policy lock
curl -s http://127.0.0.1:8077/healthz | python3 -m json.tool          # keel_policy_lock: active
curl -s http://127.0.0.1:8077/governance | grep -c "Active Keel Policy"        # expect 1
curl -s http://127.0.0.1:8077/governance | grep -c "Unknown policy rejected"   # expect 1
curl -s http://127.0.0.1:8077/governance | grep -c "Deployment Policy Locking" # expect >=1
curl -s http://127.0.0.1:8077/settings | grep -c "Policy version"              # expect 1
curl -s http://127.0.0.1:8077/governance/self-test | python3 -m json.tool
# expect: all 8 checks true, all_passed true, runtime_enforcement "pending"

# tampered policy is rejected BEFORE the runtime is called (no ledger event):
curl -s -X POST http://127.0.0.1:8077/analyze -d policy=admin_override \
  -d input_type=text --data-urlencode "evidence=test" | grep -o "No analysis was run"

# fail-closed drill (optional): mv swarmtracker_node_ui/keel_policy.json /tmp/ ,
# reload /governance (FAIL-CLOSED badge, self-test fails but unknown policies
# still rejected), then move it back.

# 6. v0.1.2 governance surfaces
curl -s -o /dev/null -w "governance %{http_code}\n" http://127.0.0.1:8077/governance
curl -s http://127.0.0.1:8077/governance | grep -c "Keel Governance Health"        # expect 1
curl -s http://127.0.0.1:8077/governance | grep -c "Next hardening step"           # expect 12
curl -s http://127.0.0.1:8077/governance | grep -c "prototype governance score"    # expect >=1
curl -s http://127.0.0.1:8077/analyze | grep -c "gov-ind-profile"                  # expect 1

# 6. v0.1.1 mode surfaces
curl -s http://127.0.0.1:8077/analyze | grep -c "Only Local Only is implemented"   # expect 1
curl -s http://127.0.0.1:8077/settings | grep -cE "not configured"                 # expect 4
curl -s -X POST http://127.0.0.1:8077/analyze -d profile_mode=public_sector \
  -d analysis_mode=cloud_assisted -d event_category=payment_ach_change \
  -d input_type=email_message --data-urlencode "evidence=Vendor requests ACH change to new account effective today." \
  | grep -oE "Public Sector Assessment|Mission Impact|roadmap — executed Local Only" | sort -u
```

## Config (all optional, via env)

`NODE_UI_OPENCLAW_URL`, `NODE_UI_OLLAMA_URL`, `NODE_UI_LEDGER`,
`NODE_UI_MODEL`, `NODE_UI_OPERATOR`, `NODE_UI_POLICY`, `NODE_UI_NODE`,
`NODE_UI_HARDWARE`, `NODE_UI_HARDWARE_NEXT`, `NODE_UI_RUNTIME`,
`NODE_UI_GENERATE_TIMEOUT`.

Non-loopback URLs are refused at startup — the UI cannot call out.

## Demo script additions (v0.1.4, ~4 min) — the product-platform story

1. **Analyze — Profile Mode cards**: five visible profiles (SMB, MSP,
   Public Sector, Critical Infrastructure, Legal / Regulated), each with a
   one-line description. Click Public Sector — event categories visibly
   change; click Legal / Regulated — its seven categories appear. The
   governance chip under the cards updates per profile.
2. **Analysis Mode cards**: click Cloud Assisted — roadmap badge + honest
   notice ("v0.1.4 will execute Local Only. No cloud calls will be made.").
3. **Evidence Type groups**: point out Security Logs — Authentication log,
   DNS log, Firewall/proxy log, Endpoint/EDR alert, Cloud/SaaS audit event.
4. **Load Sample Evidence** → `public_sector_ach_change.txt`: profile,
   mode, category, evidence type, evidence text, and claimed-sender context
   all populate. Note the "SYNTHETIC DEMO SAMPLE" label in the text itself.
5. **Run Local Analysis** → Report: Keel Trace (locked policy, Executed
   Mode: Local Only, Evidence Type, Sample Source marked synthetic), the
   Public Sector Assessment panel, and — for log evidence — the Security
   Log Assessment panel (affected asset/user, observables, additional logs
   needed, next triage step).
6. **Governance**: self-test still passes; policy lock unchanged.
7. Optional: upload `samples/auth_failed_login_burst.log` via Upload Local
   Evidence File — the text is extracted in the browser (nothing persisted),
   then Run Local Analysis; the report shows the uploaded filename.

## Demo script additions (v0.1.3, ~3 min) — the gap-closure story

1. **Governance page**: recall that v0.1.2 identified Deployment Policy
   Locking as a red control — Keel found its own gap.
2. **Active Keel Policy panel**: the node now carries a locked local policy
   file — profile, source, version 0.1.3, unknown policies denied, cloud
   escalation disabled, network disabled, tools allow/deny.
3. **Keel Policy Self-Test panel** (or `/governance/self-test` JSON):
   eight checks pass — file present/parsed, default allowed, unknown policy
   rejected, cloud disabled, network disabled, only Local Only enabled.
   Local checks only; nothing external is called.
4. **Scorecard**: Deployment Policy Locking is now YELLOW, not green —
   Node UI-side enforcement is active, but the runtime itself could still
   accept arbitrary policies if called directly. The card states the gap
   and the next hardening step (runtime enforcement or a Keel enforcement
   proxy). This is the honesty point: score improved because a control was
   added; the residual gap stays visible.
5. **Run an analysis** → Report → Keel Trace shows the policy as
   `read_only_analysis (locked)` with Policy Source and Version rows.
6. Optional live drills: POST a bogus `policy=admin_override` via curl —
   rejected before the runtime is called; or move `keel_policy.json` away
   and reload Governance — FAIL-CLOSED badge, self-test fails, but unknown
   policies are still rejected and calls fall back to read-only defaults.

Narrative: Keel does not just report gaps — it closes them incrementally,
and the UI keeps the remaining hardening work visible.

## Demo script additions (v0.1.2, ~3 min)

1. **Analyze — governance indicators**: under Profile Mode and Analysis
   Mode, each selection shows a live governance chip (Green/Yellow/Red,
   confidence, one-line reason). Flip Analysis Mode to "Cloud Assisted" —
   the chip goes yellow: no cloud path exists, Keel denies escalation.
   Local Only mirrors overall control health. Click "Governance details →".
2. **Governance page**: overall Keel Governance Health with confidence
   ("prototype governance score — not a certification"), then Attention
   Items — red/yellow rows for alarmed ledger events and weak controls.
   Note Deployment Policy Locking is honestly red: the runtime accepts
   caller-supplied policies, and the card states the exact hardening step.
3. **Scorecards**: twelve controls, each with status, confidence, current
   capability, flags, next hardening step, and source fields from real
   ledger events. Type in the search box (try a policy name or event ID),
   filter by status, toggle "alarms only" — the same filters apply to the
   recent-events table below.
4. Close on the honesty footer: control health inferred from build
   structure + audit-ledger evidence; supports audit-ready workflows; not
   a compliance attestation.

Note: overall status is computed, not staged. An alarm within the last 10
ledger events drives overall red (correct behavior — old BND-SPEC test
alarms older in the window appear as "historical" yellow items instead).
Run one clean analysis before the demo if you want the alarm out of the
recent window.

## Demo script additions (v0.1.1, ~3 min)

1. **Analyze — modes**: switch Profile Mode SMB → MSP → Public Sector →
   Critical Infrastructure and show the Event Category list changing live
   (e.g. "Emergency services message", "OT/ICS alert"). Pick Analysis Mode
   "Cloud Assisted" — the honest notice appears: only Local Only is
   implemented, no cloud calls will be made. This is the policy-routing
   story: modes are Keel decisions, not just a dropdown.
2. **Run a Public Sector analysis** (e.g. Payment/ACH change). On the
   report, walk the **Keel Trace** panel top to bottom: profile, mode (with
   "roadmap — executed Local Only" tag), category, policy, data boundary,
   Cloud Used: No, tools allowed vs denied, evidence role. Then the
   **Public Sector Assessment** panel — mission impact, data sensitivity,
   verification/escalation paths; anything the model can't assess shows
   "unknown" / "not assessed" rather than pretending.
3. **Settings**: Providers table (local active, cloud providers not
   configured — key presence alone never authorizes cloud use; Keel policy
   must permit escalation), Product Modes roadmap, and the three-tier
   Public Sector product line (Node → Agency Context Graph → Mission
   Evidence Ledger).

## Demo script (~5 min)

1. **Dashboard** — point at the status cards: node online, local runtime
   online (provider `ollama_local`, schema 0.3), local model detected via
   Ollama, ledger count, Mode: Local Only, Cloud Escalation: Disabled, Keel
   Policy. Note Hardware shows "M1 (prototype)" with Tiiny as next target —
   these are configurable status fields, not hardcoded.
2. **Analyze** — pick "Email-style message", paste a phishing-style specimen,
   add claimed org "IT", click Run Local Analysis. While it runs, mention the
   evidence goes only to 127.0.0.1.
3. **Report** — risk level, confidence, verdict, signals, recommended action,
   the real OpenClaw event ID (monospace), Cloud Used: No. Open "Raw model
   response" briefly. Point at "Org profile: not loaded" — honest about what
   doesn't exist yet.
4. **Ledger** — the event you just created is the top row. Expand the event ID
   to show the raw governed JSONL record (policy, provider, node, latency,
   governance fields).
5. **Settings** — the honesty panel: what's real vs. future, and the
   component roles (Node = appliance, Verity = cloud product, Keel = policy
   layer concept, OpenClaw = runtime under evaluation).

If the model is cold, run one warm-up analysis before the call so demo
latency stays in the 10–20 s range.
