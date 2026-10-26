# October26: single-H100 Devstral, Qwen Coder and Kolibri comparison

## Scope and allocation

One existing Lambda H100PCIe80GB, instance `DEV_102626_1xH100_80GB_pcie`,
ID `da543a79acc24d44ab6d35ec556f8196`, us-west-3. Cumulative ceilingUSD15,
warningUSD10, execution reserveUSD2 include all setup/download/load/review/idle.
Dashboard confirmsUSD3.29/hour. Guestboot2026-10-26T19:04:02Z is a conservative
estimate anchor, not a provider invoice. At20:26UTC the Usage page displayed
USD4.19 and1.27hours, despite displayed period19:04–20:26UTC; the independent
boot-time estimate was approximatelyUSD4.50. Keep these observations separate.
The earlier GLM allocation terminated18:32UTC; its providerUSD4.50 is separate.
No automatic provider termination or extra instance is authorized.

The first two models and all20responses were preserved before Kolibri was
added under the same budget. No successful comparison check was repeated.
At20:17:17UTC those model processes were stopped, GPU0MiB, temporary forwarding
removed; the operator subsequently explicitly requested Kolibri on this same
allocation. It remains allocated during that extension. An audited explicit
continuation retains original rate/start/ceiling/margin/deadline and preserves
unresolved identities and acceptance holds; it cannot reset spending.

## Frozen protocol and qualifications

Seven matched tasks at clean source `992dee01caf3698fd2ebd3666ce5c6c041901664`:
requested-configuration receipt repair; reviewed-rejection status repair;
adversarial source-gate review; and four source/evidence distinction tasks.
Frozenv3SHA256 `da662f8529091309da8fb1fa2c3a497eb74829c2bb2fb7a68a37996698ffef41`.
The independent prereview reproduced both defects and checked source/context,
known answers, exact permitted files and acceptance criteria before dispatch.
Each repair permits one initial and at most one exact-failure feedback request;
other tasks allow one attempt. No uncertain request is automatically replayed.
One serial inference lane, context16384, output2048, finite deadlines. Kolibri's
300-second deadline allows reasoning; earlier candidates had120seconds.
Sampling/tokenization/runtime/quantization differ; this is a modest task
comparison, not an isolated causal model, hardware or thinking experiment.

The original Devstral status packet lacked its full permitted source path.
That response is retained outside matched scoring. Before either valid matched
status request, the packet was amended to name the exact repository-relative
file, identically for both models, without supplying a fix. The unused original
Qwen status request and unused old feedback task were cancelled before
inference. No successful task was rerun. Kolibri receives this correctedv3
packet and original pre-fix source in separate worktrees, without prior model
patches, responses or adjudication explanations.

## Completed two-candidate results retained

| Dimension | Devstral Small2 FP8 | Qwen3-Coder-Next Q5_K_M |
| --- | --- | --- |
| Accepted matched | 2/7 | 5/7 |
| Rejected matched | 5/7 | 2/7 |
| Factually/semantically correct matched | 6/7 | 5/7 |
| Applicable, tested, independently accepted matched repairs | 1/2 | 1/2 |
| Unsupported review findings | 0 | 1 |
| Incomplete/unresolved submitted requests | 0/0 | 0/0 |
| Actual requests, including readiness/feedback/excluded context | 10 | 9 comparison +1 useful follow-up |
| Matched prompt/completion tokens | 6597/737 | 6106/924 |
| Sum matched request elapsed seconds | 32.9124 | 22.4325 |
| Matched first-content latency range seconds | 0.8913–5.5031 | 1.1288–2.3206 |
| Completion tokens / sum request elapsed seconds | 22.39 | 41.19 |
| Separate cold readiness seconds | 5.2570 | 44.0730 |

The common rate above includes prefill, transport and generation, and uses
different tokenizers. It is not native GPU decode throughput. Qwen's retained
native warm matched decode observations are129.17–139.16tokens/second;
Devstral's comparable server-only per-request decode metric was unavailable.
Cold readiness includes kernel/cache initialization and is not steady state.
Active human review effort was not instrumented. Coordinator review reasons,
feedback counts, exact failures and wall-clock events are retained privately;
no human acceptance or OS security certification is implied.

Devstral correctly answered all four evidence questions but wrapped them in
fences against explicit rawJSON instructions: four format-only rejections.
Its adversarial review correctly found no supported defect under the stated
trusted-Git/nonmalicious-filesystem assumptions; that review did not require
rawJSON. Its valid matched status proposal used a nonexistent literal and
failed applicability; one feedback repeated it. No patch was applied for that
failed task. Its separate readiness facts were right but rawJSON format failed.

Qwen returned the four evidence answers correctly in rawJSON. Its adversarial
review invented an untracked-file bypass/DoS finding despite the deliberate
clean-tree gate; rejected as unsupported, not counted as a discovered defect.
Its status proposal was a bare array instead of the required changes object,
then repeated after one feedback. Static inspection additionally found it
referenced reviewer/evidence fields absent from the original SQL projection;
no behavioral test run is claimed for the rejected proposal.

## Actual implementation and separate acceptance

Both models independently proposed an applicable receipt repair preserving
requested runtime_options as a deep copy, privacy exclusions and legacy
absence. Each went through the existing prebound executor in its own clean
checkout, actual application and28focused tests. A separate M2 reconstruction
exercised actual CustomerConfig→receipt export→retained-anchor verification,
legacy absence, nested-copy independence, privacy and tamper rejection.
Devstral's minimal allowlist addition was integrated (`3e042db`); Qwen's valid
but longer equivalent remains an accepted alternative, not a second distinct
product feature. Independent implementation commit `ab9fe162ecf92761bc70c4ce969b6d5dbb244169`.

The status-counter correction is coordinator-authored, commit
`ef8ffe805002e4bfc9a7ead1ab6d663b12724ea2`, with33focused tests. It projects
reviewer/evidence into status and removes explicitly rejected completed rows
from awaiting_review without changing historical completed state or releasing
dependencies. Neither model receives credit for this implementation.

Qwen's separate useful successor `QCReviewVisibility` fixed the Hold cell so
reviewed rejections show reviewer/reason instead of 'none recorded'. Actual
executor application,32tests and independent M2 Coordinator→Jinja rendering
checked rejected, pending and HTML-escaped metadata. Accepted implementation
`a4d0e12d79e3604f7e5b2c232b201e0589f66d0a`, integrated `14eeb5b2864d8b5e2921057b7a7253edf63ac2fe`.
One request, no feedback,3.1009seconds,2699prompt/115completiontokens.
This is separate from Qwen's5/7 matched score.

Combined code at `14eeb5b2864d8b5e2921057b7a7253edf63ac2fe` passed
**1350tests,3skipped**, one existing Starlette/httpx deprecation warning,
in80.73seconds on the MacBook. Native pilot/signing state was not installed
or changed; integrated native-app and human demo acceptance remain open.

The executor binds exact contract/proposal/source/input hashes, permitted
literal replacements and fixed finite pytest-file commands. It records a
coding_executions identity before mutation and retains diff/log/receipt.
Model text cannot choose shell commands, dependencies, acceptance or merges.
Same-user writable files and subprocess tests are not OS containment.

Private linked artifact identities (no prompts, credentials or raw outputs):

| Task | Request | Execution | Prefect flow |
| --- | --- | --- | --- |
| DSReceiptResidency | 4e70f9da1bcf4f1c8adb8f81ec9142c5 | c70e0b00d0484e9fb8b432c6a0a74dae | 63af12d7-521b-4987-a259-2d25d9ea71f2 |
| QCReviewVisibility | ef7c14323afe4469a0b93b11f1b0c483 | 6cd202b46731440c930af0fe222ece2e | d974a259-0dbc-40a5-a666-8b0b52a1b94d |

The private recovery set includes corresponding contract/proposal hashes,
review evidence and execution receipts; Prefect links use the owner's local
server at `http://127.0.0.1:4200/runs/flow-run/<flow-id>`.

## Model/runtime pins and setup observations

Devstral official `mistralai/Devstral-Small-2-24B-Instruct-2512`, revision
`55c5b41e98c2dbd21b0c8afffc540dcfc9eb5128`, consolidatedFP8+requiredmetadata,
25809991692bytes. vLLM0.29.0, build98dff2a81d747d1dba01a47f939f48c3526d4206,
officialamd64image `sha256:082ca6f035279109041ffd3fe0695cb568b29bc580b35c4f297a66a08b216c1b`,
Torch2.13.0+cu130. Native MistralTokenizerV13/mistral_common1.11.7, Mistral
load/config/tokenizer modes and tool parser. Repository Jinja retained but
not selected. No foreign reasoning kwargs; temperature0.15,top_p1,seed102626.
Runtime observedFP8 Cutlass kernel, FLASH_ATTN,24.15GiB model load and
approximately65459MiB serving reservation with caches. Download/hash339.13s;
successful launch→health96.99s. Initial cache permission failure is retained;
fixed by running the same image as uid1000 with explicit writable tmp/cache,
not rebuilding. No inference occurred in that failed launch.

Qwen official `Qwen/Qwen3-Coder-Next-GGUF`, revision
`b82fb7382639d97b38fa7672e526c760c2fb358e`, fourQ5_K_Mshards,
56710372768bytes. llama.cppb11900, commit74b104c9ba5717116b7e65e0cd35397778653304;
CUDA12.8archiveSHA256 `618c17e4e4ccc11953f21452ca34e4786a1554dc405e98a2c4d53d7c2ed5b415`;
NVIDIA CUDA12.8.1Ubuntu24image `sha256:ebef3c171eeef0298e4eb2e4be843105edf3b8b0ac45e0b43acee358e8046867`.
EmbeddedtemplateSHA256 `0b5c439b5b14126c0ae8b14ca93240c3cef1e7e29d684102b07f10727523dbdb`;
rendered assistant prefix/native thinking0 and unsupported effort capability
confirmed. No effort/templatekwargs sent; temperature1,top_p.95,top_k40,min_p0,
seed102626. Publisher row-split failed unsupported splitbuffers before inference;
layer-split succeeded with the same files/runtime. All49/49layers offloaded,
CUDAmodel53873.50MiB, CPUmapped204.02MiB separately, KV384MiB,recurrent75.38MiB,
compute146.01MiB,totalapproximately55001MiB before requests. Download/hash286.57s;
startup53.32s. Download concurrency differed, so setup times are descriptive.

Kolibri extension: selected `bartowski/Kolibri-1-GGUF` revision
`ffd6001f205725f0c4af4ff93e33c04f0f8ad9f9`, onlyQ5_K_Mtwo shards60297408576bytes.
Preflight used the same pinned upstreamb11900/CUDAenvironment: compiled
kolibri1 symbols, pinned architecture source and actualH100 device enumeration.
No Hob-forge patch or conversion assumption. GGUFheader architecturekolibri1;
embeddedtemplate accepts loweffort and local rendering verifies low instruction.
Server-supported --chat-template-kwargs reasoning_effortlow and native rendered
proof are required before dispatch. Temperature.6,top_p.95,top_k40,min_p0,
seed102626; output2048 includes reasoning. GPUload/offload and results pending
at this checkpoint; no Tiiny fit or performance claim.

Official sources: [Devstral card](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512),
[Qwen card](https://huggingface.co/Qwen/Qwen3-Coder-Next-GGUF),
[Kolibri card](https://huggingface.co/bartowski/Kolibri-1-GGUF),
[upstream Kolibri merge](https://github.com/ggml-org/llama.cpp/pull/30673).

## Preservation and recommendations at this checkpoint

Before extension:16artifactfiles82520364460bytes independently retrieved on
MacBook at exact publisher revisions and matched every GPUhash/bytecount.
M2 restored finalGPUevidencearchiveSHA256
`505db35ec433f1d7b617eec806671cf16e41db7a235d1b4885decd3382979a26`;
all20client/server streams, models, requestIDs, finish/usage/content/reasoning
matched. Original DSarchiveSHA256
`5375146566ec46da9f7ba1700d8a1607ced1edf6e3ec717d4447f4f03503ff0a`
is also retained. Fresh onlineSQLite/source checkpoints precede Kolibri.
Private packets, reasoning/output, ledgers and credentials stay excluded from
source pushes. Exact artifact preservation is not a guarantee of identical
behavior with another conversion/runtime/device.

Qwen is provisionally useful for structured evidence and governed patch
proposals, with explicit independent review because it invented a review
finding. Devstral supplied the selected minimal receipt fix and a correct
no-finding review but had repeated rawJSON formatting failures. Both repaired
only1/2 matched defects. Coordinator assistance, automated execution and
independent acceptance remain distinct. No unattended general coding autonomy,
customer-ready packaging, OS isolation or Tiiny compatibility is established.
B15packaging,B05.3integratedconfiguration,B07.5OScontainment/liveprovideronboarding,
signing and full Rohit demo/pilot acceptance remain open. Kolibri and final
closeout results will be appended without rewriting this retained checkpoint.
