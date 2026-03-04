# Directory Index

## Files

- **[operations.en.md](./operations.en.md)** - English ops guide for metrics, tuning, troubleshooting
- **[operations.md](./operations.md)** - Korean ops guide for metrics, tuning, troubleshooting

## Subdirectories

### plans/

- **[2026-02-14-benchmark-validation-reset-design.md](./plans/2026-02-14-benchmark-validation-reset-design.md)** - Design for validation runs and Kafka reset helper
- **[2026-02-14-benchmark-validation-reset-plan.md](./plans/2026-02-14-benchmark-validation-reset-plan.md)** - Plan to add AdminClient reset and validation flow
- **[2026-02-14-process-benchmark-design.md](./plans/2026-02-14-process-benchmark-design.md)** - Design for process benchmark metrics and Prometheus exporter
- **[2026-02-14-process-benchmark-implementation.md](./plans/2026-02-14-process-benchmark-implementation.md)** - Plan to implement process benchmark metrics and exporter wiring
- **[2026-02-14-refactor-microbatch-examples.md](./plans/2026-02-14-refactor-microbatch-examples.md)** - Plan to remove message processor, add batching, refresh examples
- **[2026-02-15-e2e-test-isolation-and-key-ordering.md](./plans/2026-02-15-e2e-test-isolation-and-key-ordering.md)** - Plan to isolate e2e topics and enforce key ordering
- **[2026-02-16-retry-dlq-plan.md](./plans/2026-02-16-retry-dlq-plan.md)** - Plan to add retry backoff and DLQ publishing
- **[2026-02-24-profile-analysis-design.md](./plans/2026-02-24-profile-analysis-design.md)** - Design for yappi profiling runs and analysis
- **[2026-02-24-profile-analysis-implementation-plan.md](./plans/2026-02-24-profile-analysis-implementation-plan.md)** - Plan to run and summarize yappi benchmark profiles
- **[2026-02-25-dlq-retry-cap-design.md](./plans/2026-02-25-dlq-retry-cap-design.md)** - Design verifying capped worker failures flow to DLQ
- **[2026-02-25-dlq-retry-cap-implementation-plan.md](./plans/2026-02-25-dlq-retry-cap-implementation-plan.md)** - Plan to test DLQ handling of capped failure events
- **[2026-02-25-security-hardening-design.md](./plans/2026-02-25-security-hardening-design.md)** - Design for compose secrets, DLQ minimization, validation, guardrails
- **[2026-02-25-security-hardening-implementation-plan.md](./plans/2026-02-25-security-hardening-implementation-plan.md)** - Plan to harden secrets, DLQ mode, validation, msgpack guard
- **[2026-02-26-proactive-recycling-design.md](./plans/2026-02-26-proactive-recycling-design.md)** - Design for optional process worker recycling thresholds
- **[2026-02-26-proactive-recycling-plan.md](./plans/2026-02-26-proactive-recycling-plan.md)** - Plan to add configurable worker recycling knobs
- **[2026-02-26-textual-tui-design.md](./plans/2026-02-26-textual-tui-design.md)** - Design for Textual benchmark TUI wrapper
- **[2026-02-26-textual-tui-plan.md](./plans/2026-02-26-textual-tui-plan.md)** - Plan to build Textual TUI benchmark frontend
