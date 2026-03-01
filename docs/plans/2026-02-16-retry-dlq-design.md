# Retry + DLQ Design (2026-02-16)

## Context
- Current failures are logged and committed with no retry or DLQ. `DLQ_TOPIC_SUFFIX` exists but is unused; `tenacity` is a dependency but unused.
- Goal: add bounded retries with backoff inside execution engines and publish permanently failed messages to a DLQ topic before committing offsets.

## Goals
- Configurable per-work-item retries with backoff (async + process engines).
- Preserve original `key`/`value` and attach failure metadata in headers when sending to DLQ.
- Do not commit failed offsets until DLQ publish succeeds (when DLQ enabled).
- Keep WorkManager scheduling unchanged.

## Non-goals
- No multi-topic retry tiers (no separate retry topics).
- No change to ordering semantics; retries happen per work item only.

## Configuration additions
- `ExecutionConfig`:
  - `max_retries` (default 3)
  - `retry_backoff_ms` (default 1000)
  - `exponential_backoff` (default True)
  - `max_retry_backoff_ms` (cap, default 30000)
  - `retry_jitter_ms` (default 200)
- `KafkaConfig`:
  - `dlq_enabled` (default True)
  - `dlq_topic_suffix` (reuse existing `.dlq`, now applied)

## Data model changes
- `CompletionEvent` gains `attempt: int` (1-based, final attempt on failure) and carries error text as today.
- (If needed) optional `headers: dict[str, bytes]` to pass failure metadata to DLQ publisher; otherwise headers are assembled in BrokerPoller using event fields.

## Processing flow
1) BrokerPoller consumes and enqueues to WorkManager (unchanged).
2) ExecutionEngine runs worker under retry policy:
   - On exception/timeout → wait backoff → retry until `max_retries` exhausted.
   - Emits SUCCESS with `attempt` of the success try; emits FAILURE with `attempt=max_retries` and `error`.
3) BrokerPoller handles completions:
   - SUCCESS: mark complete and commit as today.
   - FAILURE: if `dlq_enabled=True`, publish to `f"{topic}{dlq_suffix}"` with original `key`/`value` and headers (`x-error-reason`, `x-retry-attempt`, `source-topic`, `partition`, `offset`, `epoch`). After DLQ publish succeeds, mark complete and commit. If `dlq_enabled=False`, log and commit as today.
4) DLQ publish retry: use same backoff parameters; do not commit the failing offset until DLQ publish succeeds. Surface error and halt commit if publish repeatedly fails beyond retries to avoid message loss.

## Error handling and logging
- Timeouts are treated as failures and retried like other exceptions.
- Logging remains percent-style formatting. Failures include attempt count and last error.
- Backpressure behavior unchanged; retries occur inside engine tasks, so scheduling stays consistent.

## Testing plan
- AsyncExecutionEngine + ProcessExecutionEngine:
  - Success on first try; success after retry; eventual failure after `max_retries`; timeout path counts as attempt.
  - Attempt counter correctness in CompletionEvent.
- BrokerPoller DLQ path:
  - On final failure, producer receives DLQ message with expected headers/payload.
  - Offsets commit only after DLQ publish success.
  - DLQ publish failure triggers retry and blocks commit; verify no commit on repeated publish failure.
- Config parsing defaults for new fields; `dlq_enabled=False` path matches current behavior.

## Open points (resolved)
- DLQ payload: keep original `key`/`value`; add failure metadata in headers.
- Commit ordering: commit only after DLQ publish succeeds to prevent loss.
