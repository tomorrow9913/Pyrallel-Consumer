# Target Algorithm: Process Worker-Pipes

## Purpose
This is the canonical direction document for moving PR #115 beyond the v1 review-fix milestone. v1 made worker-pipes safe enough to review by fixing specific edge cases; v2 should make the algorithm explicit, ownership-bounded, and mechanically verifiable.

The target is not “make every current e2e green” in this phase. The target is an implementation-ready state model that future code changes can follow without rediscovering concurrency/recovery invariants through review comments.

## Current v1 baseline

### Main code touchpoints
- `pyrallel_consumer/execution_plane/process_engine.py`
  - owns process lifecycle, liveness scanning, registry/completion draining, worker restart, recovered-payload requeue/failure emission, and shutdown drains.
- `pyrallel_consumer/execution_plane/process_transport_worker_pipes.py`
  - owns per-worker pipe dispatch, pending dispatch tracking, worker-pipe slot semaphore, and slot-wait liveness callback.
- `pyrallel_consumer/execution_plane/process_registry_support.py`
  - owns pure-ish registry transitions for `start`, `timeout`, `done`, runtime metrics events, and dead-worker recovery extraction.
- `pyrallel_consumer/execution_plane/process_transport.py`
  - defines the transport seam and route identity.
- `pyrallel_consumer/execution_plane/process_transport_shared_queue.py`
  - remains the shared-queue compatibility transport.

### v1 review fixes that must be preserved
- Worker-pipe backpressure blocks normally; there is no fixed slot-acquire deadline for healthy long-running work.
- Slot waiting is decoupled from `task_timeout_ms`.
- Pending dispatch identity includes `worker/topic/partition/offset/id/epoch`.
- Dead worker recovery restarts the worker before requeueing recovered worker-pipe payloads.
- If restart throws after payload recovery, each recovered payload gets a terminal failure completion instead of being dropped.
- Slot-wait liveness does not recursively run recovery across unrelated threads.
- Same-thread recovery reentry is allowed through an `RLock`; cross-thread lock contention is side-effect free.
- Registry/completion mutations are serialized behind registry state locking.
- Completion prefetch discards in-flight registry rows only when `topic/partition/offset/id/epoch` match.

## Target ownership model

### 1. Engine owns lifecycle and canonical recovery decisions
`ProcessExecutionEngine` is the only component allowed to decide that a worker is dead, restart a worker, retry recovered work, emit terminal synthetic failures, or abandon work during shutdown.

The engine owns:
- worker liveness checks and restart ordering;
- registry queue draining;
- completion queue prefetch;
- in-flight registry reconciliation;
- retry/DLQ-visible synthetic completion emission;
- shutdown drain and residual diagnostics.

### 2. Transport owns local dispatch capacity, not recovery policy
`WorkerPipesProcessTransport` owns only the mechanics of delivering bytes to worker pipes and accounting for “sent but not yet accepted by worker loop” items.

The transport owns:
- route-to-worker dispatch selection;
- serialization and pipe send;
- pending dispatch entries until worker `start` acknowledgement;
- worker-pipe slot acquisition and release;
- returning pending dispatch payloads for engine-led recovery.

The transport must not:
- decide retry/DLQ outcome;
- mutate engine in-flight registry;
- recursively recover workers;
- drop payloads silently.

### 3. Registry support owns deterministic state transitions
`ProcessRegistrySupport` should remain the deterministic transition module for engine-owned registry state. It should not know about pipe semaphores, processes, or transport implementations.

### 4. Control-plane completion handling owns commits/DLQ publishing
Completion events, including synthetic terminal failures from engine recovery, are the bridge to control-plane commit/DLQ behavior. Engine recovery must surface failures in the same event language as normal worker failures.

## Canonical work identity

The logical work identity is:

```text
(topic, partition, offset, id, epoch)
```

The worker-scoped execution identity is:

```text
(worker_index, topic, partition, offset, id, epoch)
```

v1 still uses `(worker_index, topic, partition, offset)` for the in-flight registry key and stores `id/epoch` in payload. This is acceptable as a transitional implementation only if all operations that can confuse redelivery/epoch overlap compare payload identity before removing or recovering entries.

### Target invariant
Any operation that deletes, completes, times out, requeues, or emits failure for work must act on a full logical work identity, not offset alone.

### Future v2 preference
Consider widening in-flight registry keys to full worker-scoped execution identity once all callsites are ready. This would reduce identity checks hidden in payload comparisons, but it is a larger migration than the current review-fix scope.

## Target state model

A work item can be in exactly one canonical phase per execution identity:

1. `submitted_to_engine`
   - WorkManager calls engine submit.
2. `pending_dispatch`
   - Worker-pipes transport acquired a worker slot and recorded payload before sending bytes.
   - Slot is held.
3. `in_flight`
   - Worker emitted `start`; engine registry has the payload.
   - Pending dispatch entry is removed and slot is released.
4. `completed_prefetched`
   - Engine pulled completion into prefetched completion queue and removed the exact matching in-flight entry.
5. `completed_delivered`
   - `poll_completed_events` or `wait_for_completion` surfaces completion to the control plane and decrements in-flight count.
6. `recovered_pending_or_inflight`
   - Engine detected a dead worker and recovered entries from pending dispatch and/or in-flight registry.
7. `requeued`
   - Engine restarted worker and re-dispatched recovered payloads with incremented recovery attempts.
8. `terminal_failure_emitted`
   - Max retries, timeout, or restart failure surfaced a failure completion.
9. `shutdown_drained_or_abandoned_with_diagnostics`
   - Shutdown path drained visible events and reported residual state.

## Target event flow

### Submit / dispatch
1. Engine drains registry/completion opportunistically before submit when in worker-pipes mode.
2. Engine resolves route identity once from `WorkItem`.
3. Transport computes stable worker index.
4. Transport waits for the target worker slot.
   - If a liveness callback is configured, it may call engine liveness periodically.
   - The callback must be side-effect free when another thread owns the liveness lock.
5. After slot acquisition, transport writes pending dispatch entry keyed by full dispatch identity.
6. Transport serializes and sends bytes to the worker pipe.
7. If serialization/send fails, transport removes pending entry, releases slot, and raises.
8. If dispatch succeeds and this is original submission, engine in-flight count is incremented through the transport callback.

### Worker start acknowledgement
1. Worker decodes payload and emits `start` with worker-scoped offset key and full payload.
2. Engine drains registry event.
3. Engine first lets transport handle the event.
4. Worker-pipes transport removes the matching pending dispatch entry using full payload identity and releases the slot.
5. Registry support records payload in in-flight registry.

### Completion
1. Worker emits completion with `id/topic/partition/offset/epoch/status/error/attempt`.
2. Worker emits `done` for the registry key after completion enqueue when non-timeout.
3. Engine prefetch or poll decodes completion.
4. Engine discards only the in-flight entry whose topic/partition/offset plus id/epoch match the completion.
5. Control plane later processes the completion and applies commit/DLQ behavior.

### Timeout
1. Worker emits `timeout` registry event and exits fatally.
2. Engine registry marks the exact in-flight entry timed out.
3. Worker liveness recovery emits a timeout failure completion for timed-out entries and removes them from registry.
4. Timeout failures are terminal for that worker execution and should not be requeued by dead-worker recovery.

### Worker death recovery
1. Engine drains registry/completion before scanning workers.
2. For each dead worker:
   1. Recover in-flight entries owned by that worker.
   2. Recover pending dispatches owned by that worker from transport.
   3. Apply retry eligibility/max retry rules once.
   4. Restart the worker.
   5. If restart fails, emit terminal failure completions for recovered payloads.
   6. If restart succeeds, requeue recovered payloads through the transport.
3. Requeue must not recursively trigger cross-thread recovery mutation.
4. Same-thread liveness reentry may drain/reconcile only through controlled reentrant lock paths.

### Shutdown
1. Signal transport shutdown for all workers.
2. Drain registry and completion queues for a bounded period.
3. Join/terminate workers as needed.
4. Log residual in-flight registry with enough identity to diagnose unrecovered work.
5. Clear transport pending dispatch and local diagnostic state only after drains/joins complete.

## Core invariants

1. **No silent drop**: every accepted payload must eventually be in pending, in-flight, prefetched completion, delivered completion, requeued, terminal failure emitted, or explicitly logged during shutdown residual diagnostics.
2. **Identity preservation**: same topic/partition/offset with different id/epoch must remain distinct across pending dispatch, in-flight registry, completion prefetch, and recovery.
3. **Slot ownership**: worker-pipe slot is held from successful pre-send pending dispatch record until a matching worker `start` event, send failure, pending recovery, or shutdown cleanup.
4. **Single recovery owner**: only one thread may perform full liveness/recovery at a time; other slot waiters must either no-op or same-thread reenter safely.
5. **Transport is mechanical**: transport returns recoverable pending payloads but does not choose retry/DLQ semantics.
6. **Completion is authoritative for control plane**: terminal recovery outcomes must be expressed as completion events, not only logs.
7. **Drains are serialized**: registry/completion mutation paths must not run concurrently with dead-worker registry iteration.
8. **Retry count monotonicity**: recovery retries increment exactly once per recovery decision, not once per nested callback or slot wait loop.
9. **Backpressure is not failure**: full worker-pipe slots mean wait/reconcile, not timeout/fail under normal healthy workers.
10. **Shutdown is explicit**: shutdown cleanup may clear local state only after giving visible events a bounded chance to surface and after logging leftovers.

## V1 gaps versus target

1. **In-flight registry key shape is transitional**
   - Current key omits id/epoch and depends on payload checks for identity-sensitive removal.
   - V2 should evaluate widening the key or centralizing identity match helpers.

2. **State transitions are distributed**
   - Slot release occurs in transport `handle_registry_event` while registry mutation occurs in engine support.
   - V2 should make event application order and failure behavior explicit in tests and docs.

3. **Recovery and completion prefetch share mutable state**
   - v1 now serializes, but the model is still implicit.
   - V2 should centralize registry mutation APIs so all state changes pass through one small surface.

4. **Retry/DLQ semantics are split across engine and control plane**
   - Engine chooses terminal synthetic failure attempts; control plane interprets attempts for DLQ.
   - V2 should document or encode this contract more directly.

5. **Pending dispatch recovery is transport-specific**
   - Shared queue has no pending dispatch recovery; worker-pipes does.
   - V2 should define transport capability contracts explicitly.

6. **Shutdown residual handling is diagnostic-heavy**
   - Shutdown logs leftovers but does not model them as terminal events.
   - Future work should decide if shutdown should emit terminal synthetic completions or remain diagnostic-only.

## V2 implementation roadmap

### V2.1: Identity and registry API consolidation
- Add helper functions for work identity comparison and registry matching.
- Replace ad hoc `topic/partition/offset/id/epoch` comparisons with helper use.
- Consider a typed alias/dataclass for logical and execution identity.
- Tests:
  - same offset/different id/epoch cannot be removed by stale completion;
  - timeout/done/start target only intended identity where payload identity is available.

### V2.2: Explicit transport capability contract
- Document/encode whether a transport has pending dispatch recovery.
- Make `recover_pending_dispatches` return payloads plus enough identity metadata for diagnostics.
- Tests:
  - worker-pipes pending dispatch recovery releases slots exactly once;
  - shared queue returns no pending dispatches and recovery is entirely registry-based.

### V2.3: Recovery transaction shape
- Refactor `_ensure_workers_alive` into a small sequence of named operations:
  1. drain visible events;
  2. collect worker recovery candidates;
  3. restart worker;
  4. publish terminal failures or requeue;
  5. log outcome.
- Preserve restart-before-requeue ordering for worker-pipes.
- Tests:
  - restart failure emits terminal completion for in-flight and pending entries;
  - requeue happens once per recovered payload;
  - max retry entries do not requeue.

### V2.4: Backpressure/liveness contract tests
- Promote the slot-wait/liveness edge cases into clear behavioral tests.
- Tests:
  - healthy long-running worker causes blocking, not failure;
  - same-thread recovery reentry can free slots;
  - cross-thread lock contention is side-effect free and retries later.

### V2.5: Shutdown policy decision
- Decision: keep shutdown residual work diagnostic-only for V2.5; do not emit new terminal synthetic completions from shutdown.
- Rationale:
  - Shutdown is an operator/application lifecycle boundary, not evidence that a work item failed in the worker execution model.
  - Emitting synthetic failures during shutdown would make commit/DLQ behavior depend on teardown timing and could publish DLQ records after the caller has already decided to stop consuming.
  - Current shutdown already drains visible registry/completion IPC before join, logs residual in-flight work with worker/topic/partition/offset/timeout/attempt diagnostics, then clears local state.
  - Worker-pipes transport shutdown is mechanical: it sends sentinels and ignores broken senders, while pending dispatch cleanup remains local transport state cleanup.
- Minimal contract tests:
  - shutdown drains queued registry and completion events before cleanup, preserving any already-visible completion for the control plane;
  - shutdown logs residual in-flight registry diagnostics when work remains after the bounded drain;
  - shutdown delegates sentinel/close behavior through the transport seam and clears pending dispatches after joins;
  - worker-pipes shutdown ignores broken senders without converting that condition into synthetic failures.
- Minimal implementation, if needed:
  - keep existing diagnostic-only semantics;
  - add/strengthen the residual-log test before changing code;
  - include full logical identity (`id/epoch`) in the residual diagnostic string so same-offset redeliveries remain distinguishable in shutdown logs.

### V2.6: Post-join shutdown diagnostic drain
- Keep the V2.5 diagnostic-only shutdown policy.
- Add one final best-effort post-join IPC drain before local shutdown cleanup so registry/completion events that become visible while workers are joining are reconciled, counted, and preserved when they are real worker completions.
- This final drain is diagnostic/reconciliation only:
  - it must not start worker recovery;
  - it must not requeue pending dispatches;
  - it must not synthesize terminal failure completions;
  - it must happen before clearing residual in-flight registry and transport pending-dispatch state.
- Real completions drained during shutdown remain prefetched so the control plane can poll already-produced outcomes; residual work without completions remains diagnostic-only.
- Tests:
  - shutdown calls the post-join drain after worker joins and before local cleanup;
  - late completions drained post-join clear only matching registry entries and remain available to `poll_completed_events`;
  - post-join drain counts are logged;
  - existing diagnostic-only shutdown tests continue to prove no synthetic shutdown completion/requeue is emitted.

## Recommended next instruction for agents

Use this prompt for the next implementation pass:

> Read `.omx/plans/target-algorithm-process-worker-pipes.md`, `.omx/plans/prd-process-worker-pipes-target-algorithm.md`, and `.omx/plans/test-spec-process-worker-pipes-target-algorithm.md`. Treat PR #115 v1 review fixes as complete. Do not chase e2e CI failures unless a proposed v2 change directly affects them. Implement V2.1 only: centralize work identity matching for process worker-pipes registry/completion handling with minimal diff and regression tests. Do not commit `AGENTS.md`.

## Completion criteria for this document phase
- This document exists and names state owners, identities, event flows, invariants, v1 gaps, and v2 tasks.
- Team/subagent review is incorporated before committing.
- Verification: `test -s .omx/plans/target-algorithm-process-worker-pipes.md` and `git diff --check`.

## Team/Ralph review notes

- `$team ralph 3:architect` was launched as `process-worker-pipes-target-al` to parallelize review of ownership, invariants, and migration risks.
- Startup evidence: workers `%149`, `%150`, `%151` were created; `omx team status` reported `workers: total=3 dead=0 non_reporting=0` at startup.
- Worker-2 completed PRD review; worker-3 completed test-spec/doc existence review; worker-1 inspected process engine/transport/registry code and ran targeted verification commands before the team panes stopped.
- The team runtime split the task prompt too broadly into small task fragments and later reported dead workers with pending fragments, so the leader integrated the usable findings and finalized this document directly.
- Verification performed for this document phase: `test -s .omx/plans/target-algorithm-process-worker-pipes.md` and `git diff --check`.
