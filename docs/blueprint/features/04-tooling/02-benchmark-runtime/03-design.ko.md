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
| `.prof` 파일 | yappi profile 결과 |
| py-spy artifact | flamegraph/speedscope/chrometrace 등 |

## 5. 해석 규칙

- 높은 TPS는 전체 wall-clock 완료 속도를 의미한다.
- 높은 avg/p99 processing ms는 개별 메시지의 sojourn time 증가를 의미할 수 있다.
- profiling을 켠 실행은 overhead 때문에 non-profiled run과 직접 TPS 비교하면 안 된다.
- async가 높은 TPS를 보여도 queueing latency가 함께 증가할 수 있다.
- process는 CPU workload에서 유리할 수 있지만 IPC와 scheduling 비용 때문에 per-message latency가 높아질 수 있다.
