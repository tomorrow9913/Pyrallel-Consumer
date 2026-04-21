# Open Decisions

이 문서는 `pyrallel-consumer` 구현 전에 잠가야 하는 전역 정책값과 운영 기준만 모아 둔 문서다.

| 항목 | 무엇을 결정해야 하는가 | 왜 중요한가 | 옵션 | 권장안 |
| --- | --- | --- | --- | --- |
| `metadata_snapshot` 기본값 | sparse completed offset 복원을 기본으로 켤지 | reprocessing 감소와 운영 복잡도 사이의 tradeoff가 크다 | `contiguous_only`, `metadata_snapshot` | 기본값은 `contiguous_only`, 특정 사용자군에서 opt-in |
| metrics exporter packaging | facade 자동 wiring 이후 dashboard/compose integration을 어느 수준까지 canonical support로 둘지 | runtime metric 노출은 해결됐지만 운영 packaging 범위는 여전히 선택지가 있다 | runtime only, helper scripts, full bundled stack | 현재는 runtime auto-wiring + ops/test stack 문서화까지를 canonical로 유지 |
| commit strategy public surface | `on_complete` 외에 periodic commit을 public contract로 승격할지 | 문서가 현재 capability와 미래 방향을 구분해야 한다 | `on_complete` only, experimental periodic, fully supported periodic | 현재는 `on_complete`만 canonical contract로 유지 |
| process worker recycle default | `max_tasks_per_child`와 `recycle_jitter_ms`를 기본 동작으로 적극 사용할지 | 장기 실행 안정성과 예측 가능한 latency가 충돌한다 | recycle off, conservative default, aggressive recycle | 기본값은 off, ops guide에서 opt-in tuning |
| DLQ payload default | `full`과 `metadata_only` 중 무엇을 기본으로 둘지 | 장애 분석 편의와 메모리/보안 비용이 다르다 | `full`, `metadata_only`, topic별 override | 개발 기본은 `full`, production guidance는 `metadata_only` 권장 |
| ordering mode public guidance | 어떤 ordering mode를 README/예제의 기본 추천으로 둘지 | throughput과 correctness 기대치를 결정한다 | `key_hash`, `partition`, `unordered` | 일반 기본은 `key_hash`, 특수 workload별 별도 가이드 |
| benchmark release gate | benchmark 수치를 릴리즈 품질 기준으로 강제할지 | 성능 regression 관리에 중요하지만 환경 의존성이 높다 | no gate, advisory gate, fixed CI gate | advisory gate부터 시작하고 수치 기준은 문서화 |
| benchmark profiling policy | process mode profiling을 공식 지원 수준으로 올릴지 | py-spy/yappi 안정성이 플랫폼별로 다르다 | best-effort, official py-spy only, official multi-tool support | py-spy를 process 공식 경로로, yappi는 async/baseline 중심 |
