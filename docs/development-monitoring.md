# Live development monitoring

Verity can now receive metadata from an explicitly connected development harness. `/agents` groups recent contact, recently disconnected runs, and unknown status. Each run links to its durable activity record, telemetry export, and a separately retained checkpoint.

The initial adapter is `tools/monitored_development.py`: it runs an explicit developer check as an argv without a shell, submits one bounded completion to an operator-established loopback model tunnel, and samples the remote GPU through a fixed SSH `nvidia-smi` query. It does not execute model-generated tools, discover all agents, or enforce policy on arbitrary external commands.

## Meaning of the record

Identity, task descriptions, results, and remote GPU readings are integration-reported. Receipt times are recorded locally. Recent contact is not proof that a model is continuously busy. A successful request is not proof of correctness or downstream execution. Missing task state is shown as unknown. Contact loss during unfinished work is prominent; recorded interruptions remain visible after reconnection. A clock anomaly makes freshness unknown.

Prompt text, model output, tool arguments, command text, credentials and environment dumps are not fields in the telemetry schema. Unknown fields are rejected. Developer input/output files remain separate, private local evidence files; an explicit developer check can itself produce output and must be chosen accordingly.

Each workflow has a stable manifest and an existing Trust Record hash chain of observational notes. SQLite serializes writes with BEGIN IMMEDIATE and synchronous FULL. Identical event deliveries are idempotent; conflicting event IDs, invalid activity order and changed manifest identity are refused. Integrity checks fail closed before ingestion or export. Run size is bounded at 5000 entries. Whole-history replacement and truncation require an independently retained checkpoint to detect; neither hashes nor telemetry authenticate agents, prove truth or prove complete capture.

## Connect a collector

Bind the worker to loopback. Set `VERITY_MONITOR_TOKEN` to a random secret of at least 32 characters, and put that same value in an owner-only, non-symlink token file for the local collector. Ingestion is disabled when the server token is missing. Do not put this token in Git, URLs or browser forms. The POST endpoint checks a loopback peer, Host/Origin, a constant-time bearer-token comparison, bounded JSON and the schema.

Establish a private model tunnel with the verified instance-specific SSH host key, for example local port 18000 to the instance's loopback 8000. Launch the collector from the repository with `PYTHONPATH=.:apps/node` and the desired verification environment:

```
python tools/monitored_development.py \
  --monitor-url http://127.0.0.1:PORT --token-file /private/collector-token \
  --model-url http://127.0.0.1:18000 --model glm53-w4-mtp \
  --input /private/review-input.json --output /private/new-review-output.json \
  --run-id dev-unique-run --task 'Bounded code review' \
  --ssh-host root@INSTANCE_IP --host-key-alias VERIFIED_INSTANCE_ALIAS \
  --check git diff --check
```

The input is an OpenAI-compatible request body. The harness forces streaming and supplies the model alias. Output contains separate reasoning/content, finish reason, usage, client-observed first token and first visible content, elapsed time, and completion tokens divided by total request seconds. The first token includes reasoning when the runtime exposes it. `length` is recorded as a failed/incomplete request, never a completed review. SIGINT/SIGTERM preserve a failed result. Hard kills/power loss may leave an explicitly in-progress reservation. Use a new output path for each run; existing files are not overwritten.

## Independently verify

Export the run and checkpoint, retain the checkpoint independently, then run:

```
python -m verity_core.trust.verify_telemetry run.telemetry.json --checkpoint run.checkpoint.json
```

## Components considered

OpenLIT's SDK can send OTLP to a chosen destination and disable message-content capture. Its CLI has separate content-capture settings, with full capture documented as the default. No OpenLIT runtime integration or compatibility claim was made in this batch; instrumentation of our own bounded harness avoided adding a collector/platform or content-capture path. A future adapter should explicitly disable content capture and choose a local exporter.

NVIDIA DCGM Exporter supplies Prometheus-format GPU measurements and needs version-compatible DCGM. It was documentation-reviewed, not deployed. The fixed nvidia-smi sampler covers this instance test without a monitoring platform. It does not provide billing. OPA, LangGraph, pywebview and Tauri were not adopted; this work preserves existing policy code, orchestration and the native Mac shell.

Sources: https://github.com/openlit/openlit/blob/main/sdk/python/README.md ; https://github.com/openlit/openlit/blob/main/docs/latest/cli/configuration.mdx ; https://github.com/NVIDIA/dcgm-exporter
