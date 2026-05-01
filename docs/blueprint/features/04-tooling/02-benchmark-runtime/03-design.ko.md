# Benchmark Runtime Design

## 1. 문서 역할

이 문서는 benchmark CLI/TUI와 결과 해석 규칙을 고정한다.
성능 회귀 분석이나 예제 문서 업데이트 전에 먼저 읽는 문서다.

## 2. 핵심 옵션

| 옵션 | 의미 |
| --- | --- |
| `--bootstrap-servers` | Kafka bootstrap 주소 |
| `--num-messages` | 총 메시지 수 |
| `--num-keys` | key cardinality |
| `--num-partitions` | partition 수 |
| `--workloads` | `sleep,cpu,io` 부분집합 |
| `--order` | `key_hash,partition,unordered` 부분집합 |
| `--skip-baseline/--skip-async/--skip-process` | 특정 라운드 생략 |
| `--strict-completion-monitor` | completion monitor 비교 |
| `--profile` | yappi profiling |
| `--py-spy` | process worker 포함 profiling |
| `--process-batch-size` | process micro-batch size override |
| `--route-batch-size` | 같은 route lease size override. `process-batch-size`와 별도 축 |

## 3. workload 의미

| workload | 실제 동작 | 해석 포인트 |
| --- | --- | --- |
| `sleep` | `time.sleep()`으로 blocking latency 시뮬레이션 | 외부 blocking 호출 모델링 |
| `io` | `asyncio.sleep()`으로 async I/O 지연 시뮬레이션 | async mode 강점 확인 |
| `cpu` | 해시 반복으로 CPU 부하 생성 | process mode 강점 확인 |

## 4. 출력 계약

| 출력 | 설명 |
| --- | --- |
| 콘솔 표 | 각 라운드 TPS/latency 요약 |
| JSON summary | 재분석 가능한 구조화 결과 |
| route-batch JSON fields | `route_batch_size`, `items_per_input_ipc`, `items_per_completion_ipc`, `route_batch_count`, `route_batch_item_count`, `route_batch_size_avg`, `route_batch_size_max`, `completion_item_payload_count`, `completion_batch_payload_count` |
| `.prof` 파일 | yappi profile 결과 |
| py-spy artifact | flamegraph/speedscope/chrometrace 등 |

## 5. 해석 규칙

- 높은 TPS는 전체 wall-clock 완료 속도를 의미한다.
- 높은 avg/p99 processing ms는 개별 메시지의 sojourn time 증가를 의미할 수 있다.
- profiling을 켠 실행은 overhead 때문에 non-profiled run과 직접 TPS 비교하면 안 된다.
- async가 높은 TPS를 보여도 queueing latency가 함께 증가할 수 있다.
- process는 CPU workload에서 유리할 수 있지만 IPC와 scheduling 비용 때문에 per-message latency가 높아질 수 있다.
- `process_batch_size`와 `route_batch_size`는 다른 축이다. 전자는 process payload
  accumulation이고, 후자는 same-route scheduling/IPC amortization이다.
- route-batch IPC metric은 process transport 증거다. baseline/async row에서는 해당
  없는 필드를 `null`로 둔다.
- `items_per_input_ipc`와 `items_per_completion_ipc`는 실제로 IPC가 batch 단위로
  amortize되었는지를 설명한다. TPS/correctness와 함께 읽어야 한다.
