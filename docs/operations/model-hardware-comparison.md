# Model and hardware comparison record

October26 single H100PCIe80GB; text only, one serial lane. This is a small
capability baseline for later Tiiny experiments, not product/device acceptance.

| Candidate | Seven-task coding/evidence | Broader six-task suite | Appropriate next role / limit |
| --- | --- | --- | --- |
| Devstral Small2 officialFP8 | 2/7, one accepted repair | Not run | Small governed patch proposals; rawJSON issues |
| Qwen3-Coder-Next officialQ5_K_M | 5/7, one repair plus separate UI follow-up | Not run | Structured evidence/governed proposals; one false review finding |
| Kolibri1 BartowskiQ5_K_M | 5/7 after feedback, zero accepted repairs | 1/6 | Fast narrow extraction; planning/history/summary and trace-disclosure limits |
| Qwen3.8-27B officialBF16 | Not run | 4/6 | Next cloud reference for Galene/planning/history; math and harmless-summary failures |

Broader latency sums:61.75seconds Kolibri vs240.51seconds Qwen3.8; completion
counts7958 vs4929 with different tokenizers/engines/precision/sampling. Kolibri's
run was warm; Qwen's first question cold. Native low controls were verified
separately for each model. Output4096, context16K, finite300seconds, nofeedback.
Real native-tool-call-to-executor behavior and Ceres remain pending. Both final
answers resisted the harmless injection canary; Kolibri reproduced it in captured
reasoning. No confidentiality or enforced permission-boundary acceptance follows.

Actual ready GPU reservations:Devstral65459MiB,QwenCoder55001MiB,Kolibri58221MiB,
Qwen3.8 69515MiB. File bytes, model buffers and cache reservations differ. Full
artifact/runtime pins and category findings are in the [session report](../sessions/2026-10-26-coding-model-comparison.md)
and [hash manifest](../sessions/2026-10-26-model-comparison/pins.json).

All Tiiny fit/runtime/conversion/accelerator/throughput claims remain untested.
Carry exact inputs/artifacts forward; record any conversion/runtime difference
and repeat acceptance on the actual target. No equal-memory equivalence claim.
