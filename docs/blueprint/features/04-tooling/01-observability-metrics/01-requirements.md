# Observability Metrics Requirements

This document records the scope, responsibilities, and acceptance focus for `observability-metrics`.
For the preserved Korean source text, see [01-requirements.ko.md](./01-requirements.ko.md).

## 1. Document purpose

This subfeature defines the operator-facing telemetry contract for the library.
It covers both push/scrape-style Prometheus metrics and pull-style runtime diagnostics snapshots, while keeping benchmark-only summaries explicitly outside the production runtime contract.

## 2. Responsibilities

The observability surface must:

- expose parallel-consumer bottlenecks that Kafka's default consumer lag does not explain by itself;
- keep `SystemMetrics` / `PartitionMetrics` as the canonical metrics projection for exporter and benchmark observers;
- keep `RuntimeSnapshot` as the canonical read-only diagnostics projection returned by `PyrallelConsumer.get_runtime_snapshot()`;
- document adaptive backpressure, adaptive concurrency, process-batch, poison-message, and resource-signal runtime surfaces without implying engine-specific control-plane branching;
- distinguish production runtime telemetry from benchmark-only comparison artifacts;
- give operators interpretation guidance that connects telemetry to pause, lag, retry, poison-message, and tuning actions.

## 3. Functional requirements

### 3.1 Canonical runtime projections

- The runtime must expose true lag, gap count, internal queue depth, blocking duration, and pause state through `SystemMetrics` / `PartitionMetrics`.
- The runtime must expose completion success/failure counts and end-to-end latency through Prometheus-friendly counters/histograms.
- The runtime must expose metadata payload size, resource-signal state, adaptive backpressure/adaptive concurrency decisions, and process-batch runtime counters when those surfaces are available.
- The runtime snapshot API must expose queue state, retry policy, DLQ status, per-partition assignment/runtime state, and optional adaptive/process/poison-message sections as read-only diagnostics.

### 3.2 Configuration and wiring

- Prometheus export must remain opt-in through `KafkaConfig.metrics.enabled` / `KafkaConfig.metrics.port`.
- Benchmark-side metrics exposure must be documented as a harness convenience (`--metrics-port`) for Pyrallel rounds, not as proof that exporters auto-start for every runtime.
- Adaptive concurrency must be documented as changing the live control-plane `max_in_flight` limit rather than worker-process counts, async semaphore sizes, or other engine-private internals.
- Adaptive backpressure must be documented as a policy/runtime surface with its own configured guardrails and live decision output.

### 3.3 Boundary clarity

- Runtime snapshot docs must make it explicit that benchmark JSON artifacts do not serialize the full `RuntimeSnapshot`; they only carry selected lag/gap observations and benchmark results.
- Benchmark docs and observability docs must agree that baseline runs do not expose Pyrallel metrics/exporter state.
- Resource-signal docs must make it explicit that non-available states are fail-open advisory inputs rather than hard admission controls.

## 4. Non-functional requirements

- Exporter wiring must remain optional and must not become a required facade dependency.
- Metric names, labels, runtime-snapshot field names, and configuration keys must stay aligned with the current implementation.
- Operator docs must explain how telemetry should be interpreted, not just list metric names.
- Documentation must avoid implying that benchmark tooling is the source of truth for production runtime diagnostics.

## 5. Input and output boundaries

### Inputs

- `SystemMetrics` and `PartitionMetrics`
- `RuntimeSnapshot`
- completion events and end-to-end durations
- offset-commit metadata size updates
- `MetricsConfig`
- optional benchmark observers reading `SystemMetrics`

### Outputs

- Prometheus counters, gauges, and histograms
- pull-style runtime diagnostics from `PyrallelConsumer.get_runtime_snapshot()`
- operator interpretation rules and tuning guidance
- benchmark-observer summaries that reuse the same runtime projections without redefining them

## 6. Acceptance criteria

This subfeature is acceptable only if:

- the docs make the difference between true lag and Kafka's default lag explicit;
- the docs make it explicit that Prometheus export is opt-in and benchmark metrics exposure is a separate convenience path;
- adaptive backpressure and adaptive concurrency are documented as runtime policy/telemetry surfaces with configured ceilings plus live effective limits;
- the runtime snapshot API is documented as a stable read-only diagnostics surface rather than an implementation detail;
- benchmark JSON summaries are described as selected observability consumers, not as replacements for runtime snapshots or Prometheus scraping;
- resource-signal and process-batch telemetry are placed in observability docs instead of being left as undocumented side channels.
