# Context Restoration

This document is the canonical English meta-context note for the blueprint tree.
For the preserved Korean source text, see [99-context-restoration.ko.md](./99-context-restoration.ko.md).

## Role

The service-specific context in this file explains how to read the blueprint set, not what the detailed requirements or architecture are. The authoritative requirements, architecture, and design content still lives in the `01/02/03` document families.

## Canonical source history

The blueprint was reorganized from the following sources and current implementation surfaces:

- `README.md`
- `README.ko.md`
- `prd_dev.md`
- `prd.md`
- `docs/operations/guide.ko.md`
- `docs/operations/playbooks.md`
- `benchmarks/README.md`
- `examples/README.md`
- `pyrallel_consumer/config.py`
- `pyrallel_consumer/control_plane/*`
- `pyrallel_consumer/execution_plane/*`

This means the blueprint is a reorganized target-state document tree rather than a verbatim archive of earlier source texts.

## Reading assumptions that matter

- The project supports multiple execution models, but its identity is anchored on the control-plane invariant.
- `README` is a user-facing summary, while `prd_dev.md` and `prd.md` are closer to internal design sources.
- Most runtime behavior already exists in code; the blueprint mainly clarifies structure, naming, and responsibility boundaries.
- Benchmark tooling is important evidence for the product value proposition, but it is not itself the runtime contract.

## Common drift risks

- Marketing-level README phrasing and strict PRD contracts can drift if treated as interchangeable.
- Offset commit, metadata snapshots, DLQ publish, and retry exhaustion belong to one reliability chain.
- If async and process constraints are described differently across documents, the facade contract becomes unstable.
- Benchmark sample numbers will change over time, so the blueprint should preserve interpretation rules more than fixed measurements.

## When to read this file

Use this document when you need to recover why the blueprint is organized the way it is, how it maps to the codebase, or where drift tends to appear between source documents.
