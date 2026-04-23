# Kafka Runtime Ingest Requirements

This document defines the scope and acceptance criteria for the
`kafka-runtime-ingest` subfeature.
For the preserved Korean source text, see
[01-requirements.ko.md](./01-requirements.ko.md).

## 1. Document purpose

This subfeature is the ingress entrypoint for the library. It begins when the
caller constructs `PyrallelConsumer(config, worker, topic)` and ends when
Kafka records have been normalized into `WorkItem` inputs and handed to the
control plane for scheduling.

## 2. Responsibilities

- `PyrallelConsumer` must assemble the execution engine, `WorkManager`, and
  `BrokerPoller` in that order from a single `KafkaConfig`.
- `BrokerPoller` must be the only Kafka poll loop entrypoint for normal
  runtime consumption.
- The ingest runtime must own Kafka topic validation, Kafka client creation,
  main consume loop startup, and completion-monitor lifecycle.
- The ingest runtime must maintain a bounded raw payload cache when DLQ full
  payload mode is enabled.

## 3. Functional requirements

- The runtime must support a single consume topic per `PyrallelConsumer`
  instance.
- `KafkaConfig` and its nested config objects must be sufficient to derive
  consumer, producer, and admin client configuration dictionaries.
- The poll loop must use a different cadence when the system is idle than when
  there is queued or in-flight work.
- The runtime must pause Kafka fetches when control-plane load exceeds the
  configured limit and resume only after hysteresis thresholds are satisfied.
- Invalid topic names must fail before worker execution begins.
- `PyrallelConsumer.stop()` must stop the poller and then shut down the
  execution engine.
- `PyrallelConsumer.wait_closed()` must surface fatal background consume-loop
  failures without becoming the primary shutdown API.

## 4. Non-functional requirements

- The control plane must stay execution-engine agnostic. No ingest contract may
  require `BrokerPoller` or `PyrallelConsumer` to branch on async versus
  process-specific internals.
- Kafka client lifecycle paths must have explicit teardown behavior for normal
  shutdown and failed startup.
- Raw payload retention must obey a bounded memory policy and evict oldest
  cached entries first.
- DLQ payload preservation must never take priority over offset correctness or
  consumer-group liveness.

## 5. Input and output boundary

### Inputs

- `KafkaConfig`
- User worker callable
- Single consume-topic name
- Kafka record metadata: topic, partition, offset, key, and value

### Outputs

- Normalized `WorkItem` submission inputs for `WorkManager`
- Bounded raw payload cache entries used by the DLQ path
- Fetch and queue state needed to project `SystemMetrics` and runtime snapshots

## 6. MVP boundary

### Included

- Single-topic facade bootstrap
- Poll loop plus completion-monitor lifecycle
- Manual consumer, producer, and admin client initialization
- Bounded raw payload cache for DLQ full-payload mode

### Excluded

- Multi-topic orchestration
- Consumer-group discovery or management UI
- Automatic metrics exporter bootstrapping outside the explicit facade config

## 7. Acceptance criteria

- The document must make it explicit that `PyrallelConsumer` is a thin assembly
  facade, not a second scheduler or commit state owner.
- The document must make it explicit that `BrokerPoller` is both the Kafka
  ingest entrypoint and the commit coordinator.
- The document must make it explicit that the raw payload cache is a best-effort
  DLQ aid, not an authoritative delivery-state store.
