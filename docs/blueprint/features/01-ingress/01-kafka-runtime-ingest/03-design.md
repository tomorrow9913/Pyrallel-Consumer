# Kafka Runtime Ingest Design

This document captures the implementation-facing contracts for the
`kafka-runtime-ingest` subfeature.
For the preserved Korean source text, see
[03-design.ko.md](./03-design.ko.md).

## 1. Document role

Read this document before changing facade bootstrap, Kafka config surfaces, the
ingest loop, or raw payload retention behavior. It fixes the current contract
between `KafkaConfig`, `PyrallelConsumer`, `BrokerPoller`, and the first control
plane handoff into `WorkManager`.

## 2. Core configuration keys

The current implementation uses the following config surfaces as the canonical
runtime contract.

| Key | Meaning | Default |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker list used by consumer, producer, and admin client helpers | `localhost:9092` |
| `KAFKA_CONSUMER_GROUP` | Consumer group id for the source-topic consumer | `pyrallel-consumer-group` |
| `KAFKA_AUTO_OFFSET_RESET` | Initial read position for partitions without a committed offset | `earliest` |
| `KAFKA_ENABLE_AUTO_COMMIT` | Whether librdkafka auto-commit is enabled | `false` |
| `KAFKA_SESSION_TIMEOUT_MS` | Consumer session timeout | `60000` |
| `PARALLEL_CONSUMER_POLL_BATCH_SIZE` | Maximum poll batch size seen by the broker loop | `1000` |
| `PARALLEL_CONSUMER_QUEUE_MAX_MESSAGES` | Backpressure queue ceiling for queued work | `5000` |
| `PARALLEL_CONSUMER_MESSAGE_CACHE_MAX_BYTES` | Raw DLQ payload cache budget | `67108864` |
| `EXECUTION_MAX_IN_FLIGHT` | Runtime in-flight ceiling used for pause/resume hysteresis | `1000` |
| `EXECUTION_CONSUMER_TASK_STOP_TIMEOUT_MS` | How long stop waits for the consume task to exit after fetches halt | `5000` |

## 3. Facade contract

`PyrallelConsumer` is constructed with the following minimum inputs.

| Input | Description |
| --- | --- |
| `config` | Full `KafkaConfig`, including nested `parallel_consumer`, `execution`, and optional `metrics` settings |
| `worker` | Async or sync user worker accepted by the selected execution engine |
| `topic` | Single source topic consumed by this runtime instance |
| `resource_signal_provider` | Optional metrics-only provider used during metrics publication |

The facade assembles core runtime state in this fixed order:

1. Create the execution engine from `config.parallel_consumer.execution`
2. Create `WorkManager`
3. Create `BrokerPoller`

`start()` starts the poller and optional metrics loop.
`stop()` stops the poller first, publishes a final metrics snapshot, then shuts
down the execution engine.
`wait_closed()` passively surfaces fatal broker-loop errors.

## 4. Ingest output contract

Kafka records must cross the ingress boundary without losing the fields needed
for ordering, commit tracking, or DLQ fallback.

| Field | Meaning |
| --- | --- |
| `topic` | Source topic name |
| `partition` | Kafka partition id |
| `offset` | Kafka offset for commit tracking |
| `key` | Ordering input for key-hash or partition-only scheduling |
| `payload` | Value delivered to the worker path |

The ingest layer must also preserve enough context to:

- Update fetched-offset state in `OffsetTracker`
- Build `CompletionEvent` and commit-planning state later in the flow
- Project `SystemMetrics` and `RuntimeSnapshot`
- Publish a DLQ record with raw payload when the full-payload mode is enabled

## 5. Raw payload cache rules

- Cache keys are `(topic-partition, offset)` tuples.
- Raw payloads are cached only when `dlq_enabled=true`,
  `dlq_payload_mode=full`, and the configured cache budget is non-zero.
- The cache uses oldest-first eviction when adding a new payload would exceed
  the byte budget.
- If a single payload exceeds the entire cache budget, the runtime skips caching
  it and continues with warning-only degradation.
- A cache miss during DLQ publication degrades to metadata-only DLQ output
  rather than blocking offset progress.

## 6. Lifecycle rules

- `BrokerPoller.start()` owns Kafka client construction and task startup.
- The consume loop polls with a short idle timeout when the system has no
  backlog, and uses immediate drain behavior when queued or in-flight work
  already exists.
- Backpressure pauses the consumer when current load crosses the configured
  `max_in_flight` or queue ceiling, and resumes only after the 70% hysteresis
  threshold is satisfied.
- `stop()` must be idempotent at the facade and poller layers.
- `wait_closed()` is a diagnostic and fatal-error surfacing API, not a required
  step for normal successful shutdown.
