# Process Execution Engine Design

This document is the canonical English design summary for the
process-execution-engine subfeature. For the preserved Korean source text, see
[03-design.ko.md](./03-design.ko.md).

## Design contract

The process engine must document both:

- `shared_queue` as the compatibility/default path
- `worker_pipes` as the worker-affine direction

while keeping `BaseExecutionEngine.submit(work_item)` unchanged and keeping the
control plane transport-agnostic.

## Key design surfaces

- `ProcessConfig.transport_mode`
- route identity based on `(topic, partition, key)`
- transport-specific input dispatch
- single completion aggregation in the first step
- batching semantics and explicit unsupported combinations
- `wait_for_completion()` parity
- shutdown, recycle, restart, and runtime metrics semantics
