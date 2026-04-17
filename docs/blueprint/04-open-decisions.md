# Stable Public Contract Decisions (Locked for 1.0.0)

이 문서는 `GitHub #34` 기준으로 1.0.0 안정 릴리스 전에 확정해야 하는
public contract 결정을 잠근 결과다.

- 기준 이슈: https://github.com/tomorrow9913/Pyrallel-Consumer/issues/34
- 잠금 일자: 2026-04-17
- 상태: 1.0.0 gate 기준 unresolved 항목 없음

| 계약 영역 | 1.0.0 고정 결정 | 사용자/운영 기대치 |
| --- | --- | --- |
| ordering mode 기본 가이드 | 기본값은 `key_hash` | `partition`, `unordered`는 의도적 opt-in 시나리오에서만 사용한다. README/예제 기본은 `key_hash`를 유지한다. |
| DLQ payload default | 런타임 기본값은 `full` | production 운영 가이드는 `metadata_only`를 권장한다. `full` 사용 시 payload cache 예산(`PARALLEL_CONSUMER_MESSAGE_CACHE_MAX_BYTES`)을 반드시 설정한다. |
| commit semantics public surface | canonical public contract는 `on_complete`만 제공 | periodic commit은 실험/향후 확장 범위이며 1.0.0 안정 계약에는 포함하지 않는다. |
| rebalance/restart state strategy 기본값 | 기본값은 `contiguous_only` | `metadata_snapshot`은 opt-in이며, 실패 시 `contiguous_only` 의미로 fail-closed 해야 한다. |

## Notes

- metrics exporter packaging, benchmark gate/policy, worker recycle default는
  stable contract blocker가 아니라 운영 성숙도(P1/P2) 트랙에서 계속 관리한다.
