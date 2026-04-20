# Offset Commit State Requirements

This document records the scope, responsibilities, and acceptance focus for the subfeature.
For the preserved Korean source text, see [01-requirements.ko.md](./01-requirements.ko.md).

## Subfeature summary

`offset-commit-state` covers OffsetTracker state, HWM and gap progression, metadata snapshots, and true-lag projection. It belongs to the same blueprint family as the companion documents listed below.

## Focus areas

- Contiguous-safe commit advancement and gap tracking.
- Metadata encoding or hydration for sparse completed offsets.
- Metrics-facing views of lag, blockers, and commit-safe state.

## Companion documents

- [00-index.md](./00-index.md)
- [01-requirements.md](./01-requirements.md)
- [02-architecture.md](./02-architecture.md)
- [03-design.md](./03-design.md)
