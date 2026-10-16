#!/usr/bin/env bash
# Reproduce the October 15 four-H100 pilot serving configuration.
# Prerequisites: Docker/NVIDIA runtime, Python 3.12 + venv, >=210 GiB disk.
# Does not provision resources, expose public ports, or stop existing containers.
set -euo pipefail
MODEL_BASE="${1:-/scratch/models}"
SESSION_BASE="${2:-/scratch/verity-glm53-mtp}"
MODEL_REVISION=5681f7c2dc6436d63f82ab6fb892b4f5062b8304
SERVING_IMAGE=vllm/vllm-openai@sha256:0674a3ea3c971706cea310bca71daadff68ea1717c758d2f54affa5def001b86
mkdir -p "$MODEL_BASE" "$SESSION_BASE"
python3 -m venv "$SESSION_BASE/hf-venv"
"$SESSION_BASE/hf-venv/bin/pip" install 'huggingface_hub==2.2.0'
HF_HOME="$SESSION_BASE/hf-cache" "$SESSION_BASE/hf-venv/bin/python" - "$MODEL_BASE" "$MODEL_REVISION" <<'PY'
import sys
from huggingface_hub import snapshot_download
snapshot_download('canada-quant/GLM-5.3-Flash-W4A16-MTP',revision=sys.argv[2],local_dir=sys.argv[1]+'/GLM-5.3-Flash-W4A16-MTP',max_workers=12)
PY
docker pull "$SERVING_IMAGE"
docker run -d --name verity-glm-mtp --gpus all --ipc=host \
  -p 127.0.0.1:8000:8000 -v "$MODEL_BASE:/models:ro" \
  -e NCCL_ALGO=Ring -e NCCL_PROTO=Simple "$SERVING_IMAGE" \
  /models/GLM-5.3-Flash-W4A16-MTP --served-model-name glm53-w4-mtp \
  --tensor-parallel-size 4 --enable-expert-parallel \
  --max-model-len 32768 --max-num-seqs 8 --gpu-memory-utilization 0.90 \
  --no-enable-prefix-caching --disable-custom-all-reduce \
  --speculative-config '{"method":"mtp","num_speculative_tokens":2}' \
  --reasoning-parser glm45 --tool-call-parser glm47 --enable-auto-tool-choice \
  --compilation-config '{"cudagraph_mode":"FULL_AND_PIECEWISE","cudagraph_capture_sizes":[1,2,4,8],"max_cudagraph_capture_size":8}' \
  --host 0.0.0.0 --port 8000
# Poll loopback /v1/models and then make a real completion before calling ready.
# The first download hit a blocked /scratch filesystem journal for several
# minutes. Verify actual I/O progress; a full logical file size is not completion.
