# Kafka Runtime Ingest Index

This index is the canonical English entry for the subfeature document set.
For the preserved Korean source text, see [00-index.ko.md](./00-index.ko.md).

## Subfeature summary

`kafka-runtime-ingest` covers facade bootstrap, Kafka client lifecycle, consume/pause/resume behavior, and raw payload cache rules. It belongs to the same blueprint family as the companion documents listed below.

## Focus areas

- `PyrallelConsumer` facade inputs and bootstrap ordering.
- `BrokerPoller` ownership of Kafka poll, commit coordination, and runtime lifecycle.
- Pause/resume behavior, topic validation, and DLQ payload preparation.

## Companion documents

- [00-index.md](./00-index.md)
- [01-requirements.md](./01-requirements.md)
- [02-architecture.md](./02-architecture.md)
- [03-design.md](./03-design.md)
