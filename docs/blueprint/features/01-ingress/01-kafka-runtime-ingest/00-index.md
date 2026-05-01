# Kafka Runtime Ingest Index

This document is the English table of contents for the
`kafka-runtime-ingest` subfeature.
For the preserved Korean source text, see [00-index.ko.md](./00-index.ko.md).

This subfeature covers the path from the public
`pyrallel_consumer.consumer.PyrallelConsumer` facade to the Kafka-facing
runtime owned by `pyrallel_consumer.control_plane.broker_poller.BrokerPoller`.
It defines how user input becomes a running ingest loop, how Kafka clients are
constructed, when the consumer is paused or resumed, and how raw payloads are
cached for later DLQ publication.

## Questions answered by this subfeature

- What inputs are required to bootstrap the runtime?
- Which component owns Kafka `Consumer`, `Producer`, and `AdminClient`
  lifecycles?
- When does the poll loop switch between idle polling and backlog-driven
  draining?
- Where is the bounded raw payload cache maintained, and what is it allowed to
  preserve?

## Document map

| Document | Purpose |
| --- | --- |
| [01-requirements.md](./01-requirements.md) | Scope, responsibilities, and acceptance criteria for ingest runtime behavior |
| [02-architecture.md](./02-architecture.md) | Component boundaries between facade bootstrap, `BrokerPoller`, Kafka clients, and completion monitoring |
| [03-design.md](./03-design.md) | Concrete config keys, data contracts, lifecycle rules, and raw payload cache semantics |

## Suggested reading paths

- Start with [02-architecture.md](./02-architecture.md) if you need the
  bootstrap order or runtime ownership map.
- Start with [03-design.md](./03-design.md) if you need config keys, method
  contracts, or cache rules.
- Start with [01-requirements.md](./01-requirements.md) if you need the
  intended scope and acceptance bar before changing code.
