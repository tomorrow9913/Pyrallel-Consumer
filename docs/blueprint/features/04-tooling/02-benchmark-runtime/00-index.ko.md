# Benchmark Runtime Index

이 문서는 `benchmark-runtime` subfeature의 목차다.
이 subfeature는 baseline/async/process 비교 벤치마크, TUI, profiling, 결과 해석 규칙을 다룬다.

## 이 subfeature가 답하는 질문

- benchmark는 어떤 모드와 workload를 비교하는가
- CLI와 TUI는 어떻게 역할을 나누는가
- profiling은 어떤 도구를 어떤 모드에서 쓰는가
- TPS와 per-message latency는 어떻게 해석해야 하는가

## 문서 역할

| 문서 | 역할 |
| --- | --- |
| [01-requirements.md](01-requirements.ko.md) | benchmark surface의 책임과 acceptance 기준 |
| [02-architecture.md](02-architecture.ko.md) | runner, producer, baseline, pyrallel engine, stats, TUI 관계 |
| [03-design.md](03-design.ko.md) | 핵심 CLI 옵션, workload semantics, outputs, 해석 규칙 |

## 빠른 읽기 분기

- CLI 옵션과 workload 의미를 보려면 `03-design.md`
- benchmark orchestration 구조를 보려면 `02-architecture.md`
- 이 subfeature의 범위를 먼저 알고 싶으면 `01-requirements.md`
