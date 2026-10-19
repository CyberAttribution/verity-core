# Reproduction and model observations, October 19, 2026

These are preserved recipes, not instructions to provision or restart a rental.
No replacement allocation is authorized. Execute only in a separately authorized
session. Original fixed container names and paths deliberately remain unchanged;
resolve existing containers explicitly rather than deleting them automatically.

## Today: eight A100-SXM4 40 GB, one TP8 GLM replica

Repository: `wtdcode/GLM-5.3-Flash-AWQ-W4A16`.
Revision: `abd7b07719111f137e1de8a0c1b7e01c11b74d1a`.
Image: `lazymio/vllm-backport@sha256:2627d2a97e7f539b99ac98f511c589f5f520d3fc5630b195aca7f6ef865ead4e`.
Server logs report `v0.1.dev1+g29e66dad4`.
Image CUDA_VERSION is 13.0.3; observed host driver 580.105.08.
This records the working pair, not a minimum-driver compatibility claim.

The public checkpoint is AWQ W4A16, stored as compressed-tensors pack-quantized
symmetric INT4 groups of 128; activations and unquantized modules use BF16.
`glm-a10040-quantization.json` preserves the excluded modules: attention, shared
experts, embeddings, heads, visual modules and specified layers are not all INT4.
KV cache dtype auto; runtime BF16; no MTP/speculative or expert-parallel flag.
Model path `/home/ubuntu/models/GLM-5.3-Flash-AWQ-W4A16`, mounted `/model:ro`.

Exact launch argv is `glm-a10040-launch-argv.json`; original launcher and pinned
Hugging Face downloader are adjacent. The downloader requires huggingface_hub
and the original `/home/ubuntu/verity-session-20261019` evidence directory.
Download: 190,843,143,975 bytes, 22 selected files, 178.791 seconds.
Use the exact revision, verify snapshot metadata, pull the digest-pinned image,
then inspect the recorded launcher before manual execution. It waits for its
saved download receipt and image and refuses occupied GPUs. No download/load
was performed during closeout.

Launch: all eight GPUs; TP8, context8192, max sequences2, batched tokens8192,
memory utilization0.85, eager mode, custom all-reduce disabled, NCCL Ring/Simple,
reasoning parser glm45. No auto-tool parser/choice enabled in today's command.
Container cap-drop ALL, no-new-privileges, private32g shared memory, loopback
127.0.0.1:8001 to container8000, host0.0.0.0 inside the container only.
Request uses both reasoning_effort=low and
chat_template_kwargs={"reasoning_effort":"low"}. Server tokenize receipts
showed 'Reasoning Effort: Low' and a different token sequence from default.
Downloaded template SHA256:
34d5ee66b12fa6446cdae131c352b8f68cd85369e0e6fda115583805fada3891.

The earlier canada-quant checkpoint under the standard vLLM image failed A100
attention-backend selection for MLA head size512. The corrected checkpoint AND
backport image were changed together; no isolated causal ablation was run.
Successful logs selected TRITON_MLA_SPARSE, Triton FP8 MQA-logits fallback when
DeepGEMM was unavailable, and MARLIN WNA16/MarlinExperts. Sparse MLA top-k MQA
prefill fallback and first-review Triton JIT warnings remained. Loading succeeded;
this did not establish good task throughput.

Weights/loading allocation21.23GiB per worker; engine consumed22.43GiB,
peak activation1.38GiB and KV9.76GiB reported per worker. nvidia-smi observed
34,851MiB per GPU after readiness, 34,919MiB during review.
Container started18:40:04.964UTC; first readiness final18:47:24.109UTC;
stopped18:51:41.133UTC. Readiness was GLM_READY,22 input tokens,5 output tokens,
0 reasoning tokens,5 final-answer tokens,stop,2.74102179seconds end-to-end.
5/2.741=1.824 output tokens/sec end-to-end on this tiny request, NOT decode speed.
TTFT and separate prefill/decode timings unavailable.

Substantive G20261019FiniteQueue: one nonstreaming request,max2048 output tokens,
90-second client timeout, low effort, source8919characters. Input/output/reasoning/
final token counts, TTFT, final answer and precise decode elapsed time are
UNAVAILABLE because no response body/usage was returned. No synthetic token
estimate substitutes for usage. Server10-second windows showed about3.8–4.3
generation tokens/sec during most of the request (3.6 first window,3.0 tail).
Client exception is TimeoutError at about18:49:37UTC; following two tasks paused
without dispatch. No retry. Server running count was0 by18:49:39UTC, but no
explicit cancellation acknowledgement or remote final outcome was retained.
The timeout is uncertain execution, not proof of a clean cancellation. No
completed substantive GLM result or accepted GLM finding today.

## Historical eight A100 80 GB recipe: preserve separately

`historical-a10080-two-tp4-launch-argv.json` is the original recovered October14
argv, not synthesized from today's TP8 flags. Same wtdcode revision and backport
digest, GPUs0–3 and4–7, two TP4 replicas on loopback8000/8001; context32768,
sequences8,memory0.90,prefix caching,glm45 reasoning,glm47 tools/auto-tool-choice,
bounded FULL_AND_PIECEWISE CUDA graphs1/2/4/8,NCCL Ring/Simple,disable custom
all-reduce,--ipc=host. Historical local paths/mount permissions are preserved,
not endorsed as today's serving choices. Use supported low effort at request
and chat-template level; template defaults had caused earlier large reasoning
exhaustion. October14 bounded reviews completed31.98 and24.76seconds; October17
records also contain completed reviews and independently confirmed fixes.
These are historical outcomes, not today's TP8 performance measurements.

The separate canada-quant GPTQ INT4/BF16-MTP recipe, revision
5681f7c2dc6436d63f82ab6fb892b4f5062b8304, belongs to the earlier H100 session
(`../2026-10-15.md` and `../2026-10-15-benchmarks/model-launch-args.json`).
Do not label that recipe as the successful A10080 dual-TP4 deployment.
