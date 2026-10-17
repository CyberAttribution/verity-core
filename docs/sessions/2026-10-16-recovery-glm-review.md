# October 16 recovery chat: GPU access and GLM evidence

This checkpoint supersedes earlier statements that GPU access remained blocked or no GLM review occurred. The authenticated Scaleway user-data fingerprint supplied by the operator matched the ED25519 key for scw-busy-clarke. Strictly checked SSH succeeded. A fresh instance had no cached model or container; the pinned canada-quant/GLM-5.3-Flash-W4A16-MTP revision 5681f7c2dc6436d63f82ab6fb892b4f5062b8304 was downloaded and served using the previously recorded vLLM image digest. Health passed and the first review was submitted at 21:38:41 EDT.

## Reasoning control

The installed chat template accepts reasoning_effort low or high and otherwise defaults to max. Set low explicitly in both the request and chat_template_kwargs. Medium is not a supported template level. enable_thinking false did not solve this behavior. Low does not guarantee completion: use focused tasks and inspect finish_reason; length is incomplete, not approval.

## Review outcomes

Broad boundary reviews exhausted 10000 and 18000 tokens. A focused low request completed in about six seconds and flagged FIFO observation and malformed fences; fixes subsequently appear in a9e434e. The views review completed but its duplicate-event claim was mistaken: inspect validates already-stored events before append checks the incoming event. Do not move deduplication ahead of integrity validation on that advice. Other view findings require individual validation.

A focused static review of f588eec disk digest verification completed with finish_reason stop and no verified defect. This was not model execution of tests.

## Completed provider-evidence task

Commit 62afbb3 requires every event in the window to carry the expected local provider before the Provider / Model category becomes green. Empty or incomplete evidence stays yellow with an unproven explanation; unexpected providers have an accurate outside-expectation flag. Six parameterized regressions cover empty, missing, partial, fully local, external and mixed evidence. MacBook full suite: 718 passed, 2 skipped, one preexisting Starlette/httpx warning, 34.26 seconds. GLM low static review completed and reported no verified defect. No independent M2 run of this commit is claimed.

Review responses and test logs are retained beside the MacBook session backup in glm-provider-review-response.json, glm-digest-review-response.json, views-review-response.json, provider-evidence-tests.log and provider-evidence-full-tests.log. The primary session report may still contain older contradictory access statements; use this explicit correction.

Provider termination, live charges and retained volume/IP costs are not verified. The instance remains billable until provider confirmation. At approximately 22:27 EDT, elapsed compute from about 19:58 is roughly EUR 31.7 at the prior EUR 12.771/hour rate, excluding extras; this is arithmetic, not a provider bill. No automatic termination authority or spending policy is changed by this note.
