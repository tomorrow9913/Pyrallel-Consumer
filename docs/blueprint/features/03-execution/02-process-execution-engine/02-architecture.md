# Process Execution Engine Architecture

This document is the canonical English architecture summary for the
process-execution-engine subfeature. For the preserved Korean source text, see
[02-architecture.ko.md](./02-architecture.ko.md).

## Architectural comparison

The current compatibility path is:

```text
WorkManager virtual queues
  -> submit()
  -> shared multiprocessing.Queue
  -> workers compete on get()
  -> single completion queue
```

The target direction is:

```text
WorkManager virtual queues
  -> submit()
  -> route identity resolution
  -> worker-affine execution channel
  -> owner worker process
  -> single completion queue
```

## Architectural implications

- `WorkManager` stays responsible for ordering and eligibility.
- The process engine stays responsible for transport selection and dispatch.
- `shared_queue` remains the compatibility/default topology.
- `worker_pipes` is the worker-affine topology used to validate and evolve the
  long-term direction.
- Completion aggregation remains single-queue in the first step.
- Ordered modes prefer affinity preservation, not stealing.

## Evidence-backed rationale

Current benchmark and py-spy evidence suggest that ordered partition workloads
spend more time in shared-input receive paths such as
`_receive_task_payload -> multiprocessing.Queue.get -> synchronize.__enter__ -> connection.recv_bytes`
than in the actual worker function. That is why input dispatch topology is the
first architectural improvement target.
