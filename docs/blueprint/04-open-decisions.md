# Open Decisions

This document is the canonical English register of cross-cutting blueprint decisions that still need to be locked.
For the preserved Korean source text, see [04-open-decisions.ko.md](./04-open-decisions.ko.md).

| Item | Decision to lock | Why it matters | Options | Recommended direction |
| --- | --- | --- | --- | --- |
| `metadata_snapshot` default | Whether sparse completed offsets should be restored by default | It trades lower reprocessing against higher operational complexity | `contiguous_only`, `metadata_snapshot` | Keep `contiguous_only` as the default and make snapshots opt-in |
| Metrics exporter packaging | How far canonical support should extend beyond runtime metric exposure | Runtime support exists, but packaging and dashboard ownership remain policy choices | runtime only, helper scripts, full bundled stack | Keep runtime auto-wiring plus documented ops/test stacks |
| Commit strategy public surface | Whether periodic commit becomes part of the supported public contract | Docs must distinguish present guarantees from possible future direction | `on_complete` only, experimental periodic, fully supported periodic | Keep `on_complete` as the canonical contract for now |
| Process worker recycle default | Whether recycle controls should be actively enabled by default | Long-run stability and predictable latency pull in different directions | recycle off, conservative default, aggressive recycle | Keep recycle off by default and document opt-in tuning |
| DLQ payload default | Whether `full` or `metadata_only` should be the default payload mode | Diagnosis convenience trades against memory and security cost | `full`, `metadata_only`, topic-specific override | Use `full` for development defaults and recommend `metadata_only` in production |
| Ordering mode guidance | Which ordering mode should be the default recommendation in user-facing docs | This shapes throughput and correctness expectations from the start | `key_hash`, `partition`, `unordered` | Recommend `key_hash` generally and document special cases separately |
| Benchmark release gate | Whether benchmark numbers should become a release-enforced quality gate | Regression management matters, but environments remain noisy | no gate, advisory gate, fixed CI gate | Start with an advisory gate and document the thresholds |
| Benchmark profiling policy | How formal process-mode profiling support should become | `py-spy` and `yappi` behave differently across platforms | best-effort, official `py-spy` only, official multi-tool support | Treat `py-spy` as the official process path and keep `yappi` focused on async or baseline runs |
