[English](./README.md) | [한국어](./README.ko.md)

# Pyrallel Consumer

## 고성능 Kafka 병렬 처리 라이브러리


## 🔎 Search Keywords

- Python Kafka parallel consumer
- parallel consumer for Kafka in Python
- key-ordered Kafka processing

`Pyrallel Consumer`는 **Python Kafka 병렬 컨슈머(Parallel Consumer)**로, 고처리량 스트림 처리에 최적화된 라이브러리입니다.
즉, **Python에서 Kafka 병렬 컨슈머를 찾는 경우**를 대상으로 하며, **키 기준 순서 보장(key-ordered processing)**, 안정적인 오프셋 커밋, 런타임 선택형 실행 엔진(`asyncio`/multiprocessing)을 제공합니다.

Java 생태계의 `confluentinc/parallel-consumer`에서 영감을 받아, 병렬성을 극대화하면서도 데이터 정합성과 순서 보장을 유지하도록 설계되었습니다.

> **릴리즈 정책:** 현재 배포 버전은 alpha/prerelease(`0.1.2a1`)입니다. 버전/분류 정책이 alpha를 벗어나기 전까지는 `main` 브랜치를 안정화가 계속 진행 중인 hardening 브랜치로 보는 것이 맞습니다.

## 🌟 주요 특징

-   **병렬성 극대화**: Kafka 파티션 수에 얽매이지 않는 유연한 메시지 병렬 처리.
-   **정교한 순서 보장**: 메시지 키(Key) 기준으로 처리 순서를 유지하여 데이터 일관성 보장.
-   **데이터 정합성**: 리밸런싱 및 재시작 시 중복 처리를 최소화하는 '구멍(Gap) 기반 오프셋 커밋' 구현.
-   **안정성 및 가시성**: `Epoch-based Fencing`을 통한 리밸런싱 안정성 확보 및 상세 모니터링 지표 제공.
-   **유연한 실행 모델**: `AsyncExecutionEngine`과 `ProcessExecutionEngine` 중 런타임에 선택 가능한 하이브리드 아키텍처 제공.

## 📈 Observability

`PrometheusMetricsExporter` 헬퍼는 존재하지만, 현재 `PyrallelConsumer`
퍼사드는 `KafkaConfig.metrics`만으로 이 exporter를 자동 생성하거나
`/metrics` 엔드포인트를 자동 노출하지는 않습니다.

따라서 `KafkaConfig.metrics`는 지금 기준으로는 퍼사드 토글이라기보다
수동/고급 통합을 위한 설정 섹션으로 보는 편이 맞습니다.

### 노출되는 핵심 지표

| Metric | Type | Labels | 설명 |
| --- | --- | --- | --- |
| `consumer_processed_total` | Counter | `topic`, `partition`, `status` | 완료된 메시지 수 (성공/실패 구분) |
| `consumer_processing_latency_seconds` | Histogram | `topic`, `partition` | WorkManager 제출 → Completion 까지의 지연 |
| `consumer_in_flight_count` | Gauge | – | 현재 인플라이트 메시지 수 |
| `consumer_parallel_lag` | Gauge | `topic`, `partition` | True lag (`last_fetched - last_committed`) |
| `consumer_gap_count` | Gauge | `topic`, `partition` | 커밋을 막고 있는 Gap 수 |
| `consumer_internal_queue_depth` | Gauge | `topic`, `partition` | 가상 파티션 큐에 대기 중인 메시지 |
| `consumer_oldest_task_duration_seconds` | Gauge | `topic`, `partition` | Blocking offset이 막고 있는 시간 |
| `consumer_backpressure_active` | Gauge | – | Backpressure 동작 여부 (1=Pause) |
| `consumer_metadata_size_bytes` | Gauge | `topic` | Kafka 커밋 메타데이터 페이로드 크기 |

이 지표들은 `BrokerPoller.get_metrics()`와 동일한 값을 기반으로 생성되며, Grafana 대시보드 구성 시 그대로 사용할 수 있습니다.

## 📊 벤치마크 샘플 (프로파일 OFF)

최근 실행(4 partitions, 2000 msgs, 100 keys, profiling off)의 처리량(TPS)을 아래에 공유합니다. 워크로드는 `benchmarks/run_parallel_benchmark.py`의 `--workloads` 옵션으로 선택했고, 실행 시 프로파일링은 모두 비활성화했습니다.

### 워크로드 옵션이 실제로 하는 일

- `sleep` 워크로드 (`--worker-sleep-ms N`)
  - 메시지마다 `time.sleep(N/1000)`을 호출해 블로킹 지연을 시뮬레이션합니다.
  - CPU 사용은 낮고, 외부 블로킹 작업 지연 모델링에 적합합니다.

- `io` 워크로드 (`--worker-io-sleep-ms N`)
  - 메시지마다 `await asyncio.sleep(N/1000)`으로 비동기 I/O 지연을 시뮬레이션합니다.
  - 네트워크/DB 같은 I/O 바운드 시나리오를 모델링합니다.

- `cpu` 워크로드 (`--worker-cpu-iterations K`)
  - 메시지마다 `sha256` 해시 연산을 `K`회 반복하여 CPU 부하를 시뮬레이션합니다.
  - `K`가 클수록 메시지당 CPU 비용이 증가합니다.

즉, 위 옵션들은 메시지당 작업 비용을 직접 제어하는 파라미터입니다.

| Workload | 설정 | baseline TPS | async TPS | process TPS |
| --- | --- | --- | --- | --- |
| sleep | `--workloads sleep --order key_hash --worker-sleep-ms 5` | 159.69 | 2206.35 | 910.04 |
| cpu | `--workloads cpu --order key_hash --worker-cpu-iterations 500` | 2598.79 | 1403.07 | 2072.26 |
| io | `--workloads io --order key_hash --worker-io-sleep-ms 5` | 159.89 | 2797.18 | 916.60 |

> 주의: 프로세스 모드는 안정성을 위해 프로파일을 비활성화한 상태로 실행했습니다. 프로파일이 필요하면 baseline/async 위주로 사용하거나 별도 외부 프로파일러(py-spy 등)를 권장합니다.

## 🚀 아키텍처 개요

`Pyrallel Consumer`는 **Control Plane**, **Execution Plane**, **Worker Layer**로 명확하게 계층을 분리하여 설계되었습니다. `Control Plane`은 Kafka와의 통신 및 오프셋 관리를 담당하며, 어떤 `Execution Engine`이 사용되는지에 독립적으로 작동합니다. `Execution Plane`은 `Asyncio Task` 또는 `멀티프로세스`를 활용하여 사용자 정의 워커의 병렬 실행을 관리합니다.

Control Plane은 공통 `BaseExecutionEngine` 계약만 의존합니다. Process 전용 커밋
클램프 정보도 엔진 capability로 노출하므로, `BrokerPoller`가
`ProcessExecutionEngine` 구체 타입을 직접 검사하지 않아도 안전성을 유지할 수 있습니다.

```mermaid
graph TD
    subgraph "Ingress Layer (Kafka Client)"
        A["Kafka Broker"] --> B["BrokerPoller"]
    end

    subgraph "Routing Layer (Dispatcher)"
        B --> C{"Key Extractor"}
        C --> D["Virtual Partition 1"]
        C --> E["Virtual Partition 2"]
    end

    subgraph "Execution Layer (Execution Engine)"
        D --> G["ExecutionEngine.submit"]
        E --> G
        G --> H["AsyncExecutionEngine"]
        G --> I["ProcessExecutionEngine"]
        H --> J["Async Worker Task"]
        I --> K["Process Worker"]
        J & K --> L["Completion Channel"]
    end

    subgraph "Management & Control (Control Plane)"
        L --> M["WorkManager"]
        M --> N["Offset Tracker"]
        N --> O["Commit Encoder"]
    end
```

## 🛠️ 설치 및 설정

### 의존성 관리: `uv`
프로젝트의 모든 의존성 설치 및 관리는 `uv` 툴을 사용합니다.
```bash
# uv 설치 (아직 설치되지 않았다면)
pip install uv

# 프로젝트 의존성 설치
uv sync

# 개발 환경 의존성 설치 (선택 사항)
uv sync --group dev
```

### 패키지 설치/배포 (로컬 빌드)
```bash
# 로컬 설치 (editable 아님)
pip install .

# sdist/wheel 빌드
python -m pip install build
python -m build

# 생성물: dist/*.tar.gz, dist/*.whl
# 예시 업로드 (twine 사용 시)
# python -m pip install twine
# twine upload dist/*
```

### 보안/설정 메모
- Grafana(admin): `docker-compose.yml`는 `GF_SECURITY_ADMIN_PASSWORD`를 환경변수로 기대합니다. 실행 전 `.env`에 값을 넣으세요.
- DLQ: `KAFKA_DLQ_PAYLOAD_MODE`를 `metadata_only`로 설정하면 키/값 대신 헤더 메타데이터만 DLQ에 게시합니다. 기본값은 `full`(기존 동작 유지).
- 원본 DLQ payload 캐시는 `PARALLEL_CONSUMER_MESSAGE_CACHE_MAX_BYTES`(기본 `67108864`, 약 64 MiB)로 제한됩니다. 예산을 넘기면 가장 오래된 raw payload부터 제거되고, 이후 DLQ 발행은 메타데이터 전용 형태로 안전하게 degrade 됩니다.
- 라이선스: Apache-2.0

### 설정: `pydantic-settings`
환경 변수 또는 `.env` 파일을 통해 Kafka 클라이언트 및 컨슈머 설정을 관리합니다. `KafkaConfig` 클래스(pyrallel_consumer/config.py 참조)를 통해 로드됩니다.

예시 `.env` 파일:
```dotenv
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_CONSUMER_GROUP=my-consumer-group
PARALLEL_CONSUMER_EXECUTION__MODE=async # 또는 process
```

### 재시도 및 DLQ (Dead Letter Queue) 설정

Pyrallel Consumer는 실패한 메시지에 대한 자동 재시도와 DLQ 퍼블리싱을 지원합니다.

#### 재시도 설정 (`ExecutionConfig`)

재시도는 각 Execution Engine 내부에서 메시지별로 처리됩니다:

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `EXECUTION_MAX_RETRIES` | `3` | 최대 재시도 횟수 (실패 시 총 시도 횟수 = max_retries) |
| `EXECUTION_RETRY_BACKOFF_MS` | `1000` | 초기 백오프 지연 시간 (밀리초) |
| `EXECUTION_EXPONENTIAL_BACKOFF` | `true` | 지수 백오프 사용 여부 (`false`면 선형 백오프) |
| `EXECUTION_MAX_RETRY_BACKOFF_MS` | `30000` | 최대 백오프 상한선 (밀리초) |
| `EXECUTION_RETRY_JITTER_MS` | `200` | 백오프에 추가할 랜덤 지터 범위 (밀리초) |

백오프 계산 방식:
- **지수 백오프**: `min(retry_backoff_ms * 2^(attempt-1), max_retry_backoff_ms) + random(0, jitter_ms)`
- **선형 백오프**: `min(retry_backoff_ms * attempt, max_retry_backoff_ms) + random(0, jitter_ms)`

#### DLQ 설정 (`KafkaConfig`)

재시도를 모두 소진한 실패 메시지는 DLQ 토픽으로 퍼블리싱됩니다:

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `KAFKA_DLQ_ENABLED` | `true` | DLQ 퍼블리싱 활성화 여부 |
| `KAFKA_DLQ_TOPIC_SUFFIX` | `.dlq` | 원본 토픽 이름에 붙일 DLQ 토픽 접미사 |
| `KAFKA_DLQ_PAYLOAD_MODE` | `full` | `full`은 원본 key/value를 유지하고, `metadata_only`는 헤더만 전송 |
| `PARALLEL_CONSUMER_MESSAGE_CACHE_MAX_BYTES` | `67108864` | raw DLQ payload 캐시에 허용할 최대 바이트 수. 초과 시 오래된 항목부터 제거 |

DLQ 토픽으로 전송되는 메시지는 다음 헤더를 포함합니다:

| 헤더 키 | 설명 | 예시 |
| --- | --- | --- |
| `x-error-reason` | 최종 실패 에러 메시지 | `"ValueError: invalid data"` |
| `x-retry-attempt` | 최종 시도 횟수 | `"3"` |
| `source-topic` | 원본 토픽 이름 | `"orders"` |
| `partition` | 원본 파티션 번호 | `"2"` |
| `offset` | 원본 오프셋 | `"12345"` |
| `epoch` | 파티션 할당 에포크 (리밸런싱 추적용) | `"1"` |

**동작 흐름:**
1. 워커 함수 실행 실패 시 Execution Engine이 재시도
2. `max_retries` 도달 시 `CompletionEvent.status = FAILURE`, `attempt = max_retries`로 반환
3. `BrokerPoller`가 DLQ 퍼블리싱 실행 (활성화된 경우)
4. DLQ 퍼블리싱 성공 시에만 오프셋 커밋
5. DLQ 퍼블리싱 실패 시 오프셋 커밋 건너뛰고 에러 로깅

`KAFKA_DLQ_PAYLOAD_MODE=full`일 때만 raw payload 캐시가 사용됩니다. 캐시에서
항목이 축출된 뒤 최종 실패가 발생하면, 해당 메시지는 커밋을 붙잡는 대신
메타데이터 전용 DLQ 발행으로 처리됩니다.

**예제:**

## 💡 사용법

### 재시도 & DLQ 설정 (요약)
- `KafkaConfig.dlq_enabled` (기본 `True`): 실패 메시지를 DLQ로 발행할지 여부
- `KafkaConfig.DLQ_TOPIC_SUFFIX` (기본 `.dlq`): DLQ 토픽 접미사 (`<원본토픽><접미사>`)
- `ExecutionConfig.max_retries` (기본 `3`): 워커 실행 재시도 횟수
- `ExecutionConfig.retry_backoff_ms` (기본 `1000`): 재시도 대기 시작값(ms)
- `ExecutionConfig.exponential_backoff` (기본 `True`): 지수 백오프 사용 여부
- `ExecutionConfig.max_retry_backoff_ms` (기본 `30000`), `retry_jitter_ms` (기본 `200`)
- 동작: 최대 재시도 후 실패 시 DLQ로 발행(`dlq_enabled=True`), DLQ 적재 성공 시에만 커밋

예시 `.env` (발췌)
```env
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_CONSUMER_GROUP=my-consumer-group
PARALLEL_CONSUMER_EXECUTION__MODE=async            # async | process
PARALLEL_CONSUMER_EXECUTION__MAX_IN_FLIGHT=512
KAFKA_DLQ_ENABLED=true
KAFKA_DLQ_TOPIC_SUFFIX=.failed
```

```python
from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.dto import ExecutionMode, WorkItem

config = KafkaConfig()
config.dlq_enabled = True
config.DLQ_TOPIC_SUFFIX = ".failed"
config.parallel_consumer.ordering_mode = "key_hash"  # 또는 "partition" / "unordered"
config.parallel_consumer.execution.mode = ExecutionMode.ASYNC
config.parallel_consumer.execution.max_retries = 5
config.parallel_consumer.execution.retry_backoff_ms = 2000

async def worker(item: WorkItem):
    ...

consumer = PyrallelConsumer(config=config, worker=worker, topic="orders")
```

정렬 모드:
- `key_hash` (기본값): 동일 key 내 순서를 보장하면서 key 간 병렬성을 허용
- `partition`: Kafka 파티션 단위 순서를 보장
- `unordered`: 순서 보장 없이 처리량을 최우선

`worker_pool_size`는 ordered `key_hash` 라우팅 폭을 정하는 값이며,
process 워커 수를 뜻하지 않습니다.

process 모드 튜닝은 `worker_pool_size`보다
`config.parallel_consumer.execution.process_config.process_count`를 기준으로
조정하는 편이 맞습니다.

### 🏁 빠른 시작 (선택) — DLQ 설정 포함 예제

1) 설치
```bash
uv sync
```

2) 설정 + 워커 + 실행
```python
import asyncio
import hashlib
import time

from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.dto import ExecutionMode, WorkItem

config = KafkaConfig(
    BOOTSTRAP_SERVERS=["localhost:9092"],
    CONSUMER_GROUP="demo-group",
    AUTO_OFFSET_RESET="earliest",
)
config.dlq_enabled = True
config.DLQ_TOPIC_SUFFIX = ".failed"
config.parallel_consumer.execution.mode = ExecutionMode.ASYNC  # 또는 PROCESS
config.parallel_consumer.execution.max_in_flight = 512
config.parallel_consumer.execution.max_retries = 5
config.parallel_consumer.execution.retry_backoff_ms = 2000

async def io_worker(item: WorkItem):
    _ = (item.payload or b"").decode("utf-8")
    await asyncio.sleep(0.005)

def cpu_worker(item: WorkItem):
    data = item.payload or b""
    for _ in range(500):
        data = hashlib.sha256(data).digest()

def sleep_worker(item: WorkItem):
    _ = (item.payload or b"").decode("utf-8")
    time.sleep(0.005)

consumer = PyrallelConsumer(
    config=config,
    worker=io_worker,  # 또는 cpu_worker / sleep_worker
    topic="demo-topic",
)

async def main():
    await consumer.start()
    try:
        await asyncio.sleep(60)
    finally:
        await consumer.stop()

asyncio.run(main())
```

3) 실행 엔진 선택 팁
- I/O 바운드: `ExecutionMode.ASYNC`, async 워커 사용
- CPU 바운드: `ExecutionMode.PROCESS`, picklable sync 워커 사용
- 동시 처리량: `max_in_flight`를 먼저 조정하고, process 모드에서는 `process_count`를 함께 조정

For detailed examples including async mode, process mode, configuration tuning, and graceful shutdown patterns, see the **[`examples/`](./examples/)** directory.

### 리밸런스 상태 보존 정책

- 기본값: `contiguous_only`
  - 리밸런스/재시작 시에는 현재 안전한 contiguous HWM만 Kafka committed offset으로 남깁니다.
  - HWM 뒤의 sparse 완료 오프셋은 이후 다시 처리될 수 있으며, 이것이 가장 단순하고 안전한 at-least-once 기본값입니다.
- 옵션: `metadata_snapshot`
  - revoke/commit 시 sparse 완료 오프셋을 Kafka commit metadata에 인코딩하고, 다음 assignment에서 복원합니다.
  - 불필요한 재처리를 줄일 수 있지만, 실패 시에는 반드시 `contiguous_only` 수준으로 fail-closed 해야 합니다.

`metadata_snapshot`을 사용하더라도 다운스트림 side effect는 여전히 멱등적으로 설계하는 것이 권장됩니다.

## 🧪 벤치마크 실행

`benchmarks/run_parallel_benchmark.py` 스크립트는 프로듀서 → 베이스라인 컨슈머 → Pyrallel (async/process) 순서로 벤치마크를 자동 실행합니다. Kafka가 로컬에서 실행 중이라면 다음과 같이 사용할 수 있습니다.

```bash
uv run python benchmarks/run_parallel_benchmark.py \
  --bootstrap-servers localhost:9092 \
  --num-messages 50000 \
  --num-keys 200 \
  --num-partitions 8
```

- 콘솔에는 각 러운드별 TPS / 평균 / P99 지연이 표 형태로 출력됩니다.
- JSON 리포트는 기본적으로 `benchmarks/results/<UTC 타임스탬프>.json`에 저장됩니다.
- 인자 없이 실행하면 Textual TUI가 열려 워크로드/오더링/프로파일링 옵션을 대화형으로 선택할 수 있습니다.
- `--skip-baseline`, `--skip-async`, `--skip-process` 플래그를 통해 특정 라운드를 건너뛸 수 있습니다.
- `--workloads sleep,cpu` 형태로 워크로드 부분집합을, `--order key_hash,partition` 형태로 오더링 모드 부분집합을 한 번에 실행할 수 있습니다.
- `--strict-completion-monitor on,off`를 사용하면 completion monitor 모드 비교 벤치마크를 한 번에 실행할 수 있습니다.
- 기본 동작으로 AdminClient를 사용해 벤치마크 토픽과 컨슈머 그룹을 삭제 후 재생성하여 이전 실행의 레그가 섞이지 않습니다. 클러스터 권한이 없거나 수동 제어가 필요한 경우 `--skip-reset` 플래그로 재설정을 비활성화할 수 있습니다.
- 워커가 느려질 때는 `--timeout-sec` 값(기본 60초)을 늘려 async/process 라운드의 타임아웃을 조정할 수 있습니다.

## 🧪 E2E 테스트 실행

```bash
# 로컬 Kafka 시작
docker compose up -d kafka-1

# Kafka-backed end-to-end 테스트 실행
uv run pytest tests/e2e -q
```

- `localhost:9092`에 Kafka가 없으면 E2E 테스트는 즉시 실패하지 않고 skip 됩니다.
- 실제 Kafka 경로를 확인하려면 로컬 `docker compose` 스택을 띄운 뒤 실행하면 됩니다.
- 테스트용 모니터링 스택은 `docker compose -f .github/e2e.compose.yml up -d`로 띄울 수 있습니다.
- 테스트 스택 대시보드:
  - Prometheus: http://localhost:9090
  - Grafana: http://localhost:3000 (`local-e2e`)

## 📖 문서

-   **`prd_dev.md`**: 개발자를 위한 요약 문서. 프로젝트의 주요 기능, 아키텍처, 개발 방법론 등을 간결하게 설명합니다.
-   **`prd.md`**: 상세 설계 해설서. 각 컴포넌트의 의도, 기술 선정 이유, 인터페이스 정의 등 "왜"라는 질문에 대한 깊이 있는 답변을 제공하는 문서입니다.
-   **`docs/ops_playbooks.md`**: 운영 플레이북과 튜닝 가이드. 프로필별 권장 설정, 장애 대응, 모니터링/알람 기준, 튜닝 절차를 제공합니다.

## 📊 모니터링 스택 (Prometheus + Grafana)

- `docker-compose.yml`에 Prometheus(9090), Grafana(3000), Kafka Exporter(9308), Kafka UI(8080), Kafka(9092)가 포함됩니다.
- Prometheus 설정은 `monitoring/prometheus.yml`에서 관리하며 기본으로 `kafka-exporter`와 호스트의 Pyrallel Consumer(메트릭 포트 9091)를 스크랩합니다. 컨슈머가 컨테이너 내에서 돌면 해당 주소를 컨테이너 호스트네임으로 변경하세요.

사용 방법
1) 현재 퍼사드 기준 참고:
- `config.metrics.enabled = True`만으로는 `PyrallelConsumer`가 `/metrics`
  엔드포인트를 자동 노출하지 않습니다.
- 이 스택은 `PrometheusMetricsExporter`를 직접 wiring 한 커스텀 통합이나
  하위 레벨 구성과 함께 사용하는 전제를 둡니다.

2) 스택 실행:
```bash
docker compose up -d
```

3) 확인:
- Prometheus: http://localhost:9090 (target 상태 확인)
- Grafana: http://localhost:3000 (`.env`의 `GF_SECURITY_ADMIN_PASSWORD` 사용)

4) Grafana 데이터소스 추가:
- Type: Prometheus
- URL: http://prometheus:9090
- Access: Server

5) 대시보드:
- Kafka Exporter 기본 지표 + Pyrallel Consumer `consumer_*` 메트릭을 선택하여 그래프 패널을 추가하면 됩니다.
- 예시 쿼리: `consumer_processed_total`, `consumer_processing_latency_seconds_bucket`, `consumer_in_flight_count`.
- process batch 관측 예시: `consumer_process_batch_flush_count{reason="timer"}`, `consumer_process_batch_avg_size`, `consumer_process_batch_buffered_age_seconds`.

## 🤝 기여하기

모든 커밋 메시지는 [Conventional Commits](https://www.conventionalcommits.org/ko/v1.0.0/) 스펙을 따릅니다.

---
© 2026 Pyrallel Consumer Project
