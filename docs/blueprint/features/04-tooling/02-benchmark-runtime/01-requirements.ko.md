# Benchmark Runtime Requirements

## 1. 문서 목적

이 문서는 `benchmark-runtime`의 책임과 요구사항을 정의한다.
이 subfeature는 `pyrallel-consumer`의 처리량/지연 특성을 baseline, async, process 모드에서 비교 가능한 형태로 제공하는 평가 도구다.

## 2. 책임

- baseline consumer와 `pyrallel-consumer` async/process 모드를 같은 workload 조건에서 비교해야 한다.
- CLI와 TUI 양쪽에서 benchmark를 실행할 수 있어야 한다.
- JSON summary와 profiling artifact를 저장해야 한다.
- workload와 ordering mode 차이를 결과 해석 규칙과 함께 설명해야 한다.

## 3. 기능 요구사항

- `sleep`, `io`, `cpu` workload를 선택적으로 실행할 수 있어야 한다.
- `key_hash`, `partition`, `unordered` ordering 모드를 비교할 수 있어야 한다.
- baseline/async/process 라운드를 개별적으로 skip 할 수 있어야 한다.
- profiling은 async/baseline과 process에서 각각 현실적인 도구 경로를 제공해야 한다.
- topic/group reset을 통해 이전 실행 찌꺼기를 줄일 수 있어야 한다.

## 4. 비기능 요구사항

- benchmark 결과는 재실행 가능한 CLI 옵션과 함께 남아야 한다.
- profiling overhead가 throughput 비교를 왜곡할 수 있다는 점이 문서에 드러나야 한다.
- TUI는 CLI 기능을 숨기지 않고 discoverability를 높이는 보조 shell이어야 한다.

## 5. 입력/출력 경계

입력:

- Kafka bootstrap servers
- 메시지 수, key 수, partition 수
- workload/order/profiling 옵션

출력:

- 콘솔 요약 표
- JSON summary
- `.prof` 또는 py-spy artifact
- TUI 실행 상태

## 6. acceptance 기준

- baseline/async/process가 같은 harness에서 비교된다는 점이 문서에 명확해야 한다.
- TPS와 per-message latency가 다른 의미를 가진다는 점이 설명돼야 한다.
- profiling이 benchmark 수치와 분리해서 해석돼야 한다는 경고가 포함돼야 한다.
