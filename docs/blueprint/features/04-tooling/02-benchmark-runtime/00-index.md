# Benchmark Runtime Index

`benchmark-runtime` defines the benchmark harness used to compare the baseline consumer with Pyrallel async and process execution modes under the same workload matrix.
The English documents in this directory are the canonical contract; the preserved Korean source text is available beside each file.

## Questions this subfeature answers

- Which benchmark dimensions are first-class inputs, and which are fixed by the harness?
- How do the CLI runner, Textual TUI, producer, baseline consumer, and Pyrallel test harness divide responsibilities?
- When are topics and consumer groups reset or lazily created during a run?
- How should TPS, total runtime, per-message latency, profiling artifacts, and optional metrics exposure be interpreted together?
- How do JSON summaries feed the current release-gate evaluator without turning the benchmark harness itself into a release-policy engine?

## Document map

| Document | Role |
| --- | --- |
| [01-requirements.md](./01-requirements.md) | Scope, responsibilities, inputs/outputs, and acceptance criteria for benchmark tooling |
| [02-architecture.md](./02-architecture.md) | Runtime component boundaries between the runner, reset helpers, execution rounds, stats pipeline, and TUI |
| [03-design.md](./03-design.md) | Concrete CLI/TUI options, workload semantics, artifact contracts, and result interpretation rules |

## Quick reading guide

- Start with [03-design.md](./03-design.md) for the current CLI surface and output contracts.
- Read [02-architecture.md](./02-architecture.md) to understand when reset, produce, consume, stats, profiling, and metrics wiring happen.
- Read [01-requirements.md](./01-requirements.md) first if you need the benchmark feature boundary before editing code or docs.

## Current implementation anchors

- Primary runner: `benchmarks/run_parallel_benchmark.py`
- Pyrallel harness: `benchmarks/pyrallel_consumer_test.py`
- Topic/group reset helper: `benchmarks/kafka_admin.py`
- Producer helper: `benchmarks/producer.py`
- Baseline consumer: `benchmarks/baseline_consumer.py`
- Result summarization: `benchmarks/stats.py`
- Interactive shell: `benchmarks/tui/*`
