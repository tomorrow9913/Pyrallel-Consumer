# Pyrallel Consumer - 개발 현황 및 인수인계 문서

*최종 업데이트: 2026년 2월 26일 목요일*

## 최근 업데이트 (2026-02-26)
- Gap 타임아웃 가드 추가: `ParallelConsumerConfig.max_blocking_duration_ms`(기본 0=비활성). BrokerPoller가 `get_blocking_offset_durations`를 기준으로 임계 초과 시 강제 실패 이벤트를 생성하고, WorkManager 경로를 통해 DLQ/커밋 처리. 관련 단위 테스트 추가 (`tests/unit/control_plane/test_blocking_timeout.py`).
- Shutdown 드레인 보강: BrokerPoller 종료 시 남은 completion 이벤트/타임아웃 이벤트를 한 번 더 처리해 커밋 누락을 최소화.
- MetadataEncoder 견고화: Bitset 인코딩을 hex 기반으로 변경해 메타데이터 크기 축소, RLE/Bitset 모두 초과 시 sentinel("O")로 overflow 표시, decode 실패 시 fail-closed(set 반환) + 경고 로깅. 신규 단위 테스트 추가(`tests/unit/control_plane/test_metadata_encoder.py`).
- .env/.env.sample의 주석 포함 값으로 인한 Pydantic 검증 오류를 제거: `KAFKA_AUTO_OFFSET_RESET`, `EXECUTION_MODE` 값을 순수 값으로 정리하여 단위 테스트 (`test_blocking_timeout.py`) 실패를 해소.
- 커버리지 보강:
  - PrometheusMetricsExporter에 registry 주입 옵션 추가, HTTP 서버 비활성 시 no-op 보장, gauge/counter/histogram 동작 검증 테스트 추가 (`tests/unit/metrics/test_prometheus_exporter.py`).
  - ProcessExecutionEngine 헬퍼들에 대한 단위 테스트 추가(`_decode_incoming_item` oversize/error, msgpack decode, `_calculate_backoff` 지터 포함) (`tests/unit/execution_plane/test_process_engine_helpers.py`).

## 최근 업데이트 (2026-02-25)
- DLQ retry-cap: 프로세스 엔진의 `worker_died_max_retries` 경로를 커버하는 단위 테스트 추가 (`tests/unit/execution_plane/test_process_execution_engine.py`, `tests/unit/control_plane/test_broker_poller_dlq.py`).
- 모니터링 스택 스모크 테스트: `docker compose up -d kafka-1 kafka-exporter prometheus grafana` 후 확인.
  - Prometheus `/-/ready` OK, active targets 중 `kafka-exporter` health=up, `pyrallel-consumer` health=down은 앱 미실행으로 정상.
  - Grafana `/api/health` OK (10.4.2, DB ok).
  - Kafka exporter `/metrics` 응답 확인.
  - 테스트 후 `docker compose down`으로 정리 완료.
- 패키징 준비: `pyproject.toml`에 build-system(setuptools/wheel), 메타데이터(3.12+, classifiers, keywords) 정리. `python -m build`로 sdist/wheel 빌드 성공(`dist/` 생성). 라이선스는 임시 `Proprietary` 텍스트 사용 중이며 setuptools에서 라이선스 필드 테이블은 추후 SPDX 문자열로 전환 필요(경고 발생).
- 라이선스 전환: 프로젝트 라이선스를 Apache-2.0으로 지정, LICENSE 파일 추가, `pyproject.toml`에 SPDX 표현/라이선스 파일 설정 반영. `python -m build` 재확인 성공.
- 보안 하드닝 (2026-02-25):
  - `docker-compose.yml`의 Grafana admin 비밀번호 하드코딩 제거 → env 주입(`GF_SECURITY_ADMIN_PASSWORD=${...:?missing}`), `.env.sample`에 placeholder 추가.
  - DLQ payload 모드 추가: `KAFKA_DLQ_PAYLOAD_MODE=metadata_only` 시 key/value 미전송, 기본 `full` 유지. 토픽/접미사 화이트리스트 검증 추가.
  - 토픽/로그 검증 유틸 추가(`pyrallel_consumer/utils/validation.py`) 적용.
  - msgpack 역직렬화 크기 제한(`msgpack_max_bytes`, 기본 1,000,000) 및 decode 가드 추가.
  - 관련 단위 테스트 추가/통과, `python -m build` 재확인 (라이선스 경고만 남음).

## 목차
- 1. 프로젝트 철학 및 목표
- 2. 현재 폴더 구조 및 개편 방향
- 3. 현재까지의 진행 상황 (v1 → v2)
- 4. 다음 진행 계획, Phase별 진행 현황 및 테스트 요약

## 1. 프로젝트 철학 및 목표 (From prd_dev.md)

본 프로젝트는 Java 생태계의 `confluentinc/parallel-consumer`에서 영감을 받아, Python `asyncio` 환경에 최적화된 **고성능 Kafka 병렬 처리 라이브러리**를 개발하는 것을 목표로 합니다.

### 1.1. 핵심 목표
- **Kafka Control Plane 단일화**: 실행 모델(Async/Process)에 관계없이 Kafka 통신, 오프셋 관리, 리밸런싱 로직은 단일 코드로 유지합니다.
- **실행 모델 공존**: 단일 릴리즈 내에 `AsyncExecutionEngine`과 `ProcessExecutionEngine`을 모두 제공하며, 런타임 설정으로 선택 가능하게 합니다.
- **GIL 제약 회피**: `ProcessExecutionEngine`을 통해 CPU-bound 작업을 위한 구조적 해결책을 제공합니다.
- **확장성**: `ExecutionEngine` 인터페이스를 통해 새로운 실행 모델을 추가할 수 있는 구조를 갖습니다.

### 1.2. 설계 철학
> This project treats execution models as interchangeable, but treats offset correctness as sacred.

> 이 프로젝트는 실행 모델을 교체 가능한 부품으로 취급하지만, 오프셋의 정확성은 신성불가침으로 다룹니다.

### 1.3. 아키텍처 원칙
- **Control Plane**: Kafka 소비, 리밸런싱, 오프셋 관리, 상태 제어를 담당
- **Execution Plane**: 메시지의 병렬 처리, 실행 모델(Async/Process) 구현을 담당
- **Worker Layer**: 사용자의 비즈니스 로직 실행을 담당

**핵심 원칙**: Control Plane은 현재 어떤 Execution Engine이 사용되는지 절대 인지하지 못해야 합니다.

### 1.4. 개발 시 참고 지침
- **주요 참고**: `prd_dev.md`를 기본 개발 명세서로 참고합니다
- **보충 참고**: `prd_dev.md`에 명시되지 않은 세부사항이나 의문사항이 발생할 경우 `prd.md`를 참고하여 원본 설계 의도를 확인합니다
- **우선순위**: `prd_dev.md` > `prd.md` 순서로 적용하되, 두 문서 간 충돌 시 `prd_dev.md`의 개발자 친화적 명세를 우선으로 적용합니다
- **커밋 정책**: 기능 단위(예: 테스트 1개 작성, 버그 1개 수정 등) 작업이 끝날 때마다 즉시 커밋합니다. Agent 세션이 중단되어도 작업 흐름이 끊기지 않도록, 변경사항은 단계별로 잘게 나누어 커밋해 두어야 합니다.
- **인수인계 최우선 원칙**: `GEMINI.md` 업데이트는 모든 작업에서 최우선 순위입니다. Agent 세션은 언제든 종료될 수 있으므로, **각 단계(테스트 1개 작성, 버그 1개 수정 등)를 완료할 때마다 즉시 `GEMINI.md`를 업데이트**합니다. 작업 완료 후 일괄 업데이트가 아닌, 단계별 점진적 업데이트를 수행합니다.
- **기타사항**: 만약 인계사항이 많은 경우 별도 파일을 git이 볼수 없는 영역에 기록하고 그 파일 경로를 기록합니다.
    로그에 출력하는 변수들은 f 표현식이 아닌 % 표현식으로 사용하여 파싱에 오류가 없도록 합니다.
    같은 행위를 반복하고 있는 경우 즉시 작업을 종료하고 사용자의 판단을 물어봅니다.
    **프로덕션 코드에는 `assert` 구문을 사용하지 마십시오. 대신 명시적인 예외(`ValueError`, `RuntimeError` 등)를 발생시키십시오. `assert`는 테스트 코드에서만 허용됩니다.**

## 2. 현재 폴더 구조 및 향후 개편 방향

### 2.1. 현재 구조 (v1 기반)
프로젝트의 핵심 로직은 `pyrallel_consumer` 패키지 내에 각 파일의 역할에 따라 분리되어 있습니다.

- **`pyrallel_consumer/`**:
    - **`__init__.py`**: 패키지 초기화 파일입니다.
    - **`constants.py`**: 프로젝트 전역에서 사용될 상수를 정의합니다. (현재 비어있음)
    - **`config.py`**: `pydantic-settings` 기반의 `KafkaConfig` 클래스를 정의하여 Kafka 클라이언트 설정을 관리합니다. 환경 변수에서 설정을 로드할 수 있습니다. `dump_to_rdkafka` 유틸리티 메서드를 포함하여 Pydantic 설정 객체를 `librdkafka` 호환 딕셔너리로 변환할 수 있습니다.
    - **`logger.py`**: `LogManager`를 통해 프로젝트 전반의 로깅을 관리합니다. (현재는 플레이스홀더)
    - **`consumer.py`**:
        - `ParallelKafkaConsumer` 클래스가 위치한 프로젝트의 핵심 파일입니다.
        - 메시지 소비, 병렬 처리 태스크 생성, Kafka 클라이언트 라이프사이클 관리를 총괄합니다.
        - `OffsetTracker`와 `worker` 모듈을 사용하여 실제 오프셋 관리와 역직렬화 작업을 위임합니다.
    - **`offset_manager.py`**:
        - `OffsetTracker` 클래스를 정의합니다.
        - `SortedSet`을 사용하여 처리 중인 메시지의 오프셋을 파티션별로 추적하고, 안전하게 커밋할 수 있는 오프셋(`safe_offset`)을 계산하는 역할을 담당합니다.
    - **`worker.py`**:
        - `batch_deserialize` 함수를 제공하여 메시지 역직렬화를 담당합니다.
        - `orjson`을 사용하며, `ThreadPoolExecutor`에서 실행되어 메인 이벤트 루프의 부하를 줄입니다.

- **`tests/`**:
    - 프로젝트의 테스트 코드를 관리합니다.
    - **`tests/unit/`**: 각 모듈의 개별 기능 단위를 격리하여 테스트합니다. (예: `OffsetTracker`의 `mark_complete` 기능)
    - **`tests/integration/`**: 여러 모듈 간의 상호작용 또는 실제 외부 시스템(Kafka, DB 등)과의 연동을 테스트합니다. (예: `ParallelKafkaConsumer`의 전체 메시지 처리 흐름)

### 2.2. v2 아키텍처에 따른 개편 방향
`prd_dev.md`의 3계층 아키텍처를 반영하여 다음과 같이 구조를 개편할 예정입니다.

```
pyrallel_consumer/
├── __init__.py
├── constants.py
├── config.py
├── logger.py
├── dto.py                    # CompletionEvent, TopicPartition 등 DTO 정의
├── control_plane/            # Control Plane 레이어
│   ├── __init__.py
│   ├── broker_poller.py      # Kafka 소비 및 리밸런싱
│   ├── offset_tracker.py     # SortedSet 기반 상태 머신
│   ├── work_manager.py       # Blocking Offset 스케줄러
│   └── metadata_encoder.py   # RLE/Bitset 이중 인코딩
├── execution_plane/          # Execution Plane 레이어
│   ├── __init__.py
│   ├── base.py              # ExecutionEngine 추상 인터페이스
│   ├── async_engine.py      # AsyncExecutionEngine 구현
│   └── process_engine.py    # ProcessExecutionEngine 구현
└── worker_layer/            # Worker Layer (기존 worker.py 확장)
    ├── __init__.py
    ├── async_worker.py      # Async 워커 유틸리티
    └── process_worker.py    # Process 워커 래퍼
```

**개편 원칙**:
- Control Plane은 Execution Engine의 존재를 인지하지 못함
- 각 레이어는 명확한 인터페이스를 통해 통신
- 기존 코드는 점진적으로 새 구조로 마이그레이션

## 3. 현재까지의 진행 상황 (v1 → v2)

### 3.1. v1 완료 사항 (기존 코드베이스 정리)
`prd.md` 설계에 따라 기존 코드베이스의 문제를 해결하고 구조를 개선하는 1단계 작업을 완료했으며, 설계 문서를 개편했습니다.

- **중복 코드 및 버그 수정**:
    - `parallel_consumer.py`와 `consumer.py`로 중복되던 파일을 `consumer.py`로 통합했습니다.
    - `_run_consumer` 메소드가 중복 정의되던 문제를 해결했습니다.
    - 오프셋 추적 시 `defaultdict`에 잘못 접근하던 치명적인 버그(`AttributeError`)를 수정했습니다.

- **관심사 분리 (SoC) 리팩토링**:
    - `consumer.py`에 혼재되어 있던 기능들을 `offset_manager.py`와 `worker.py`로 분리하고, `consumer.py`가 이들을 사용하도록 구조를 변경했습니다.
    - 오프셋 관리 로직은 `OffsetTracker` 클래스로 완전히 위임했습니다.
    - 메시지 역직렬화는 `worker.py`의 `batch_deserialize` 함수를 사용하도록 변경했습니다.

- **프로젝트 구조 개선**:
    - `src` 디렉토리에 의존하던 잘못된 `import` 경로를 수정했습니다.
    - `config.py`와 `logger.py`를 패키지 내에 추가하여 설정과 로깅을 위한 기반을 마련했습니다.
    - `pydantic-settings`를 `KafkaConfig`에 적용하여 `.env` 파일 로딩 기능을 추가했습니다.

### 3.2. v2 설계 검토 및 개편 (2026-02-08)
Oracle 분석을 통해 기존 설계 문제들을 식별하고 `prd_dev.md`로 개편했습니다.

- **설계 문제 해결**:
    - 명명 통일성 문제 해결 ("Pyrallel Consumer" vs "PAPC")
    - 구현 격차 문제 해결 (기본 T1-T2 → 전체 T1-T11 로드맵 명시)
    - 가상 파티션 설계 모호성 개선 (Key Extractor 개념 도입)
    - 메타데이터 인코딩 전략 구체화 (RLE + Bitset 이중 인코딩)

- **아키텍처 재설계**:
    - 3계층 아키텍처 도입 (Control Plane, Execution Plane, Worker Layer)
    - ExecutionEngine 추상화를 통한 Async/Process 엔진 공존 구조
    - Epoch Fencing을 통한 리밸런싱 안정성 확보
    - 관측성 모델 도입 (True Lag, Gap, Blocking Offset 지표)

- **TDD 전략 수립**:
    - Contract Testing을 통한 ExecutionEngine 호환성 보장
    - Phase별 구현 계획 수립 (Control Plane → Execution → Async → Process)
    - 단위/통합/계약 테스트 피라미드 구성

### 3.3. v2 아키텍처 구현 진행 상황 (신규)

`prd_dev.md`의 TDD 실행 순서에 따라 **Phase 1 – Control Plane Core** 구현이 완료되었으며, **Phase 2 – WorkManager & Scheduling**의 일부가 진행 중입니다.

#### 2026-02-26 – ProcessExecutionEngine 프로액티브 워커 리사이클링
- `ProcessConfig`에 `max_tasks_per_child`(기본 0, 비활성)와 `recycle_jitter_ms`(기본 0) 추가.
- 프로세스 워커가 작업을 처리할 때마다 카운트하며, `max_tasks_per_child + jitter`에 도달하면 현재 배치에서 남은 WorkItem을 재큐잉 후 종료; 부모 `_ensure_workers_alive()`가 동일 인덱스에 새 워커를 재시작.
- 로깅: `%` 포맷 사용 (`ProcessWorker[%d] recycling after %d tasks (limit=%d, jitter=%d)`). 타임아웃 예외 메시지도 `%` 포맷으로 교체.
- 테스트: `pytest tests/unit/execution_plane/test_process_execution_engine.py -vv` 전체 12건 통과. 브로커 통합: `pytest tests/integration/test_broker_poller_integration.py -vv` 통과.
- DLQ 퍼블리시: `dlq_payload_mode`가 모킹된 설정에 없을 때 `DLQPayloadMode.FULL`을 기본값으로 사용해 통합 테스트 통과.
- 커밋 메타데이터: `MetadataEncoder.encode_metadata` 결과를 Kafka commit 오프셋에 세팅(메타데이터 None 방지).

- **v2 아키텍처 구조 개편**:
    - `control_plane`, `execution_plane`, `worker_layer` 디렉토리 구조를 생성했습니다.
    - 레이어 간 통신을 위한 `dto.py` 파일을 정의하고 DTO들을 구현했습니다.
    - 기존 `consumer.py`를 삭제하고, `pyrallel_consumer/control_plane/broker_poller.py`로 리팩토링을 시작했습니다.

- **`OffsetTracker` 구현 완료 (TDD 우선순위 1)**:
    - `SortedSet`을 사용한 `OffsetTracker`를 구현하고, `mark_complete`, `advance_high_water_mark`, `get_gaps` 등의 핵심 로직을 완성했습니다.
    - 단위 테스트를 작성하고 모든 테스트가 통과하는 것을 확인했습니다.
    - Epoch Fencing 연동을 위해 `increment_epoch`, `get_current_epoch` 등의 메서드를 추가했습니다.
    - **Blocking Offset duration 추적 기능 추가**: `get_gaps` 호출 시 blocking offset의 시작 시간을 기록하고, `get_blocking_offset_durations` 메서드를 통해 blocking offset의 지속 시간을 노출하는 기능을 추가했습니다.

- **`MetadataEncoder` 구현 완료 (TDD 우선순위 2)**:
    - RLE와 Bitset을 동시에 인코딩하여 더 짧은 결과물을 선택하는 `MetadataEncoder`를 구현했습니다.
    - 단위 테스트를 작성하고 모든 테스트 통과를 확인했습니다.

- **Rebalance & Epoch Fencing 구현 완료 (TDD 우선순위 3)**:
    - `BrokerPoller`가 파티션별로 `OffsetTracker`를 관리하도록 리팩토링을 완료했습니다.
    - `_on_assign`, `_on_revoke` 콜백에서 Epoch를 관리하는 로직을 구현했으며, 관련 테스트에서 발생했던 `NameError` 및 타입 힌트 불일치 문제를 해결했습니다.
    - 메시지 처리 중 Epoch Fencing 로직을 `_process_virtual_partition_batch`에 구현하여 이전 세대의 좀비 메시지를 안전하게 폐기합니다.
    - `_on_revoke`에서 `MetadataEncoder`를 사용하여 최종 커밋 메타데이터를 생성하고 Kafka에 전달하는 로직을 구현했습니다.

### 3.4. v2 아키텍처 구현 진행 상황 (WorkManager 및 ExecutionEngine 연동)

`prd_dev.md`의 TDD 실행 순서에 따라 **Phase 1 – Control Plane Core**의 `WorkManager` 구현이 완료되었으며, **Phase 2 – WorkManager & Scheduling**의 일부가 진행 중입니다.

*   **`dto.py` 업데이트**:
    *   `WorkItem` DTO에 `id: str` 필드 추가.
    *   `CompletionEvent` DTO에 `id: str` 필드 추가.
    *   `Any` 타입을 사용하기 위해 `from typing import Any` 임포트 추가.
*   **`pyrallel_consumer/execution_plane/base.py` 생성**:
    *   `ExecutionEngine`의 추상 인터페이스인 `BaseExecutionEngine` 클래스를 정의했습니다. 이는 `WorkManager`가 `ExecutionEngine`의 구체적인 구현을 알지 못하고도 상호작용할 수 있도록 하는 DI(Dependency Injection)의 핵심 경계 역할을 합니다.
*   **`WorkManager` 핵심 로직 구현**:
    *   생성자에서 `BaseExecutionEngine` 인스턴스를 주입받도록 변경하여 Control Plane과 Execution Plane 간의 의존성을 명확히 했습니다.
    *   `_in_flight_work_items` 맵과 `_current_in_flight_count` 변수를 추가하여 `WorkManager`가 현재 처리 중인 메시지(Work-in-Flight)의 총 수를 직접 관리하도록 했습니다. 이는 전반적인 시스템 부하 추적 및 백프레셔(Backpressure) 구현의 기반이 됩니다.
    *   `submit_message` 메서드에서 각 `WorkItem`에 `uuid` 기반의 고유 `id`를 할당하도록 변경했습니다. 이 `id`는 작업의 생명주기 동안 고유하게 식별되며, `WorkManager`가 `in-flight` 상태의 작업을 추적하고 완료 이벤트를 매칭하는 데 사용됩니다. `submit_message`는 이제 내부 큐에 메시지를 추가하는 역할만 수행하며, 실제 ExecutionEngine으로의 제출은 `_try_submit_to_execution_engine`에 의해 별도로 관리됩니다.
    *   `_try_submit_to_execution_engine` 메서드를 개선했습니다. 이 메서드는 `max_in_flight_messages`를 초과하지 않는 범위 내에서 가상 파티션 큐에서 `WorkItem`을 가져와 `ExecutionEngine`의 `submit` 메서드로 작업을 제출합니다. 특히, "Lowest blocking offset 우선" 스케줄링 정책을 구현하여 HWM 진행을 막고 있는 메시지를 우선적으로 처리하도록 했습니다.
    *   `poll_completed_events` 메서드를 수정했습니다. `ExecutionEngine`으로부터 완료 이벤트를 주기적으로 폴링하고, 수신된 `CompletionEvent`의 `id`를 사용하여 `_in_flight_work_items`에서 해당 `WorkItem`을 제거하고 `_current_in_flight_count`를 감소시킵니다. 작업 완료 후에는 `_try_submit_to_execution_engine`을 다시 호출하여 처리 가능한 새 작업을 제출하도록 시도합니다.
    *   `OffsetTracker` 초기화 시 필수 인자 (`topic_partition`, `starting_offset`, `max_revoke_grace_ms`)를 전달하도록 `on_assign` 메서드를 수정했습니다.
    *   `get_total_in_flight_count` 메서드가 `WorkManager`의 `_current_in_flight_count`를 반환하도록 변경했습니다.
    *   `mark_complete` 호출 시 `epoch` 인자를 제거하여 `OffsetTracker`의 시그니처와 일치시켰습니다. (테스트 코드 수정 완료)
    *   `get_blocking_offsets` 메서드를 `OffsetTracker`의 `get_gaps`를 사용하도록 리팩토링했습니다.
    *   `on_assign`과 `on_revoke` 메서드에 `_rebalancing` 플래그를 추가하여 리밸런스 중 메시지 제출을 차단하는 로직을 구현했습니다.
*   **`WorkManager` 관측성(Observability) 기능 추가**:
    *   `get_gaps()` 메서드를 추가하여 각 토픽-파티션별 완료되지 않은 오프셋 범위(갭) 정보를 노출합니다.
    *   `get_true_lag()` 메서드를 추가하여 각 토픽-파티션별 실제 지연(last fetched offset - last committed offset) 정보를 노출합니다.
    *   `get_virtual_queue_sizes()` 메서드를 추가하여 각 가상 파티션 큐의 현재 크기 정보를 노출합니다.
*   **`BrokerPoller` `mypy` 오류 수정**:
    *   `WorkManager` 생성자에 `BaseExecutionEngine`을 전달하도록 수정했습니다.
    *   `submit_message` 호출 시 누락되었던 `key`와 `payload` 인자를 `msg.key()`와 `msg.value()`로 각각 전달하도록 수정했습니다.
    *   `CompletionStatus`를 import하고, 완료 이벤트 상태 비교 로직을 `CompletionStatus.FAILURE`를 사용하도록 수정했습니다.
*   **`tests/unit/control_plane/test_work_manager.py` 업데이트 및 디버깅**:
    *   `WorkManager` 생성자에 `mock_execution_engine`을 전달하도록 수정하고, 테스트 픽스처들을 관련 변경사항에 맞춰 업데이트했습니다.
    *   `OffsetTracker` 클래스의 생성자 변경사항을 반영하기 위해 `unittest.mock.patch`를 사용하여 `OffsetTracker` 클래스를 모킹했습니다. 이를 통해 `WorkManager.on_assign`이 `OffsetTracker`를 인스턴스화할 때 올바른 인자를 전달하는지 확인하고, 모킹된 `OffsetTracker`의 동작을 제어할 수 있게 했습니다.
    *   `test_submit_message`는 이제 메시지가 내부 큐에 올바르게 추가되고 추적되는지 확인한 후, `_try_submit_to_execution_engine`을 명시적으로 호출하여 ExecutionEngine으로의 제출을 검증합니다.
    *   `test_poll_completed_events`에서 `ExecutionEngine.poll_completed_events`를 모킹하여 완료 이벤트를 반환하도록 하고, 이들이 `WorkManager`에 의해 올바르게 처리되고 `_current_in_flight_count`가 감소하며 `_in_flight_work_items`에서 제거되는지 검증했습니다.
    *   `OffsetTracker`에서 `get_blocking_offset` 메서드가 제거됨에 따라 이와 관련된 테스트 코드 및 Mock 설정을 제거 및 수정했습니다.
    *   `test_on_assign_and_on_revoke` 테스트에서 발생한 어설션 논리 오류 (해지되지 않은 파티션이 `_offset_trackers`에 남아있어야 하는 부분)를 수정했습니다.
    *   `mark_complete` 호출의 인자가 `offset` 하나만 받도록 변경된 구현과 테스트 코드가 일치하도록 수정하여 `AssertionError`를 해결했습니다.
    *   새롭게 추가된 `test_prioritize_blocking_offset` 테스트를 통해 "Lowest blocking offset 우선" 스케줄링 정책이 올바르게 작동함을 검증했습니다.
    *   `test_no_submission_during_rebalance` 테스트를 추가하여 리밸런스 중 메시지 제출이 차단됨을 검증했습니다.
    *   `test_get_gaps` 및 `test_get_true_lag` 테스트를 추가하여 `WorkManager`의 새로운 관측성 기능들이 올바른 값을 반환하는지 확인했습니다.
    *   `test_get_virtual_queue_sizes` 테스트를 추가하여 가상 파티션 큐의 크기 정보가 올바르게 노출되는지 검증했습니다.
    *   `NameError: name 'Any' is not defined` 및 `ModuleNotFoundError: No module named 'pyrallel_consumer'` 오류 등 디버깅 과정을 거쳐 모든 단위 테스트가 성공적으로 통과하도록 만들었습니다.

### 3.5. v2 아키텍처 설계 문서 업데이트
- **prd.md 업데이트**: `Control Plane`과 `Execution Plane`의 계약에 대한 핵심 원칙을 설명하는 섹션을 추가했습니다.
  - `ExecutionEngine.submit()`의 Blocking 특성과 `WorkManager`의 역할 명시
  - `max_in_flight` 설정의 이중적 의미(Control Plane vs. Engine) 명시
  - `get_in_flight_count()`의 올바른 사용법(참고용) 명시
- **prd_dev.md 업데이트**:
  - `ExecutionEngine` 인터페이스에 `submit()` Blocking에 대한 경고 추가
  - 설정 스키마 예제를 `max_in_flight_messages`와 `max_concurrent_tasks`로 구분하여 업데이트
  - Contract Test 항목에 Control Plane의 카운터 의존성 금지 테스트 명시

### 3.6. BrokerPoller 현황 및 문제점 (2026-02-09 추가)

기존 `GEMINI.md`에 명시된 `BrokerPoller`의 문제점들을 해결하기 위한 작업을 진행했으며, 현재 통합 테스트 단계에서 새로운 문제들에 직면했습니다.

#### 3.6.1. 완료된 작업
- **Deadlock 수정**: `BrokerPoller`가 `WorkManager`의 작업 스케줄링을 트리거하지 않던 문제를 해결했습니다. `WorkManager._try_submit_to_execution_engine`을 `schedule()`이라는 public 메서드로 변경하고 `BrokerPoller`의 메시지 제출 루프 이후에 호출하도록 수정했습니다.
- **중복 로직 제거**: `BrokerPoller`와 `OffsetTracker`에 중복으로 존재하던 'in-flight' 오프셋 추적 로직을 제거하고, `WorkManager`를 단일 진실 공급원(Single Source of Truth)으로 삼도록 리팩토링했습니다.
- **메타데이터 커밋 로직 수정**: `BrokerPoller._run_consumer`의 주기적인 오프셋 커밋 로직에서, `OffsetTracker`의 상태가 변경되기 전에 메타데이터가 올바르게 인코딩되도록 수정하여 커밋 정확성을 보장했습니다.
- **Hydration (상태 복원) 기능 구현**:
  - `MetadataEncoder`에 누락되었던 `decode_metadata` 메서드를 구현하고 단위 테스트를 추가하여, 커밋된 메타데이터를 다시 오프셋 집합으로 변환할 수 있게 만들었습니다.
  - `OffsetTracker.__init__` 메서드가 초기 오프셋 집합을 받을 수 있도록 수정했습니다.
  - `BrokerPoller._on_assign` 콜백에 Hydration 로직을 구현하여, 리밸런싱 시 컨슈머가 이전에 커밋된 메타데이터를 읽어 `OffsetTracker`의 상태를 복원하도록 했습니다.

#### 3.6.2. 통합 테스트 (`test_broker_poller_integration.py`) 문제 해결

`BrokerPoller`의 핵심 로직인 `_run_consumer`에 대한 통합 테스트(`tests/integration/test_broker_poller_integration.py`)를 작성하고 디버깅하는 과정에서 다음과 같은 문제점들을 해결했습니다.

1.  **`OffsetTracker` Mocking 복잡성 해결**:
    *   **문제**: 초기 `OffsetTracker` Mocking은 `MagicMock`이 내부 상태 변경을 제대로 시뮬레이션하지 못하여 `mark_complete.call_count`가 0으로 집계되는 문제가 있었습니다.
    *   **해결**: `mock_offset_tracker_class` 픽스처를 사용자 정의 `DummyOffsetTracker` 클래스로 리팩터링했습니다. 이 클래스는 `mark_complete`, `advance_high_water_mark`, `get_current_epoch` 등 필요한 메서드를 명시적으로 구현하고 자체 내부 상태(`last_committed_offset`, `completed_offsets`, 호출 추적 리스트 등)를 관리하도록 하여, `MagicMock`의 복잡한 `side_effect` 설정에서 발생할 수 있는 예측 불가능한 동작을 제거했습니다. 테스트 어설션도 `DummyOffsetTracker`의 내부 호출 추적 리스트를 직접 사용하도록 변경했습니다.

2.  **`consumer.consume` Mocking 및 `StopIteration` 오류 해결**:
    *   **문제**: `mock_consumer.consume`의 `side_effect`가 제공된 메시지 목록을 모두 소진하면 `StopIteration` 예외가 발생하여 `asyncio.to_thread` 컨텍스트 내에서 `RuntimeError`로 변환되는 문제가 있었습니다.
    *   **해결**: `custom_consume_side_effect`를 구현하여 `mock_consumer.consume`이 항상 리스트(메시지가 없으면 빈 리스트)를 반환하도록 함으로써 `StopIteration` 예외 발생을 방지했습니다.

3.  **`BrokerPoller` 커밋 로직 실행 문제 해결**:
    *   **문제**: `BrokerPoller`의 `_run_consumer` 루프에서 완료 이벤트가 처리된 후에도 Kafka 커밋이 트리거되지 않는 문제가 있었습니다. 이는 두 가지 원인이었습니다:
        1.  `_run_consumer` 내의 `if not messages: continue` 문이 Kafka 메시지가 소비되지 않은 루프 반복에서 커밋 로직의 실행을 막았습니다.
        2.  `mock_consumer.committed`가 `-1` 오프셋을 가진 `KafkaTopicPartition`을 반환했을 때, `BrokerPoller`는 이를 유효한 커밋 오프셋으로 해석하여 `last_committed_offset`을 `-1 - 1 = -2`로 잘못 계산했습니다. 이로 인해 `OffsetTracker`의 `potential_hwm` 계산이 실패하여 커밋 조건이 충족되지 않았습니다.
    *   **해결**:
        1.  `_run_consumer` 내의 `continue` 문을 제거하고 로직 흐름을 재구성하여, 새 Kafka 메시지가 소비되지 않더라도 완료 이벤트 처리 및 커밋 로직이 모든 루프 반복에서 항상 실행되도록 했습니다.
        2.  `mock_consumer.committed.return_value`를 `KafkaTopicPartition("test-topic", 0, OFFSET_INVALID)`로 변경하여 "커밋된 오프셋 없음" 상태를 올바르게 시뮬레이션했습니다. 이는 `BrokerPoller._on_assign`이 `OffsetTracker`의 `last_committed_offset`을 `0 - 1 = -1`로 올바르게 초기화하도록 했습니다.
        3.  `OFFSET_INVALID` 상수를 `confluent_kafka`에서 임포트하여 `NameError`를 해결했습니다.

4.  **테스트 견고성 및 결정론적 동작 개선**:
    *   **문제**: 기존 테스트의 임의적인 `asyncio.sleep` 호출은 테스트의 비결정성 및 경쟁 조건을 유발했습니다.
    *   **해결**: 임의의 `asyncio.sleep` 호출을 타임아웃이 있는 명시적인 `while` 루프(메시지 제출, 완료 처리, 커밋 트리거 등)로 대체하여 테스트의 견고성과 결정론적 동작을 크게 향상시켰습니다.
    *   `mock_work_manager` 픽스처를 리팩터링하여 `poll_completed_events`가 `asyncio.Queue`를 사용하도록 하고, 테스트에서 이벤트를 주입하는 `_push_completion_event` 헬퍼 메서드를 추가하여 비동기 이벤트 전달을 보다 결정론적으로 만들었습니다.

**현재 상태**: `test_broker_poller_integration.py` 통합 테스트가 이제 성공적으로 통과합니다.

### 3.7. 설계 불일치 수정 (2026-02-09)

PRD 문서(prd.md, prd_dev.md)와 실제 구현 간의 설계 불일치를 분석하고, 코드와 테스트를 수정하여 모든 테스트(76개)가 통과하도록 정비했습니다.

#### 3.7.1. 코드 수정 사항

1. **`WorkManager.schedule()` 재귀 호출 → while 루프 전환**:
   - `work_manager.py`의 `schedule()` 메서드가 `await self.schedule()`로 재귀 호출되어 스택 오버플로 위험이 있었습니다.
   - `while True:` 루프로 전환하고, 작업이 없거나 용량이 가득 찬 경우 `return`으로 탈출하도록 수정했습니다.

2. **`ProcessExecutionEngine.submit()` 이벤트 루프 블로킹 수정**:
   - `process_engine.py`의 `submit()`에서 `self._task_queue.put(work_item)`이 직접 호출되어 async 이벤트 루프를 블로킹하는 문제가 있었습니다.
   - `await asyncio.to_thread(self._task_queue.put, work_item)`으로 변경하여 이벤트 루프 블로킹을 방지했습니다.

3. **`ProcessExecutionEngine.shutdown()` 잘못된 설정 참조 수정**:
   - `shutdown()`에서 `self._config.async_config.task_timeout_ms`를 사용하여 프로세스 join timeout을 계산하고 있었습니다. Process 엔진이 Async 설정에 의존하는 것은 설계 위반입니다.
   - `ProcessConfig`에 `worker_join_timeout_ms: int = 30000` 필드를 추가하고, `self._config.process_config.worker_join_timeout_ms`를 참조하도록 수정했습니다.

4. **`broker_poller.py` 로거 f-string → % 포맷 전환**:
   - GEMINI.md의 개발 지침("로그에 출력하는 변수들은 f 표현식이 아닌 % 표현식으로 사용")에 따라 모든 f-string 로거 호출을 % 포맷으로 변환했습니다.
   - 총 약 20개 인스턴스를 변환했습니다.

#### 3.7.2. 테스트 수정 사항

1. **`test_work_manager.py` 메서드 호출명 동기화**:
   - 테스트에서 `_try_submit_to_execution_engine()`을 호출하고 있었으나, 실제 구현은 `schedule()`로 변경되어 있었습니다.
   - 4개 호출 지점을 `schedule()`로 수정했습니다.

2. **`test_offset_tracker.py` `in_flight_offsets` 테스트 수정**:
   - `in_flight_offsets`가 계산 프로퍼티(symmetric_difference)인데 직접 `.add()`를 호출하는 테스트가 있었습니다.
   - `update_last_fetched_offset()`을 호출하여 올바르게 in-flight 상태를 만든 후 테스트하도록 수정했습니다.

3. **테스트 패키지 `__init__.py` 추가**:
   - `tests/`, `tests/unit/`, `tests/unit/execution_plane/`, `tests/integration/` 디렉토리에 `__init__.py`가 누락되어 `from tests.unit.execution_plane.test_execution_engine_contract import ...` 임포트가 실패하던 문제를 해결했습니다.

4. **통합 테스트 `OffsetTracker` Mock 수정**:
   - `mock_offset_tracker_class`가 `MagicMock(spec=OffsetTracker)`로 생성되었으나, `mocker.patch`에서 `new=instance`로 설정할 때 `OffsetTracker(...)`가 `instance.return_value`(빈 MagicMock)를 반환하여 side_effect가 적용되지 않는 문제가 있었습니다.
   - `tracker_mock.return_value = tracker_mock`을 추가하여 Mock이 호출 시 자기 자신을 반환하도록 수정했습니다.

#### 3.7.3. 테스트 결과
- **수정 전**: 56 passed, 5 failed
- **수정 후**: 76 passed, 0 failed

앞으로의 모든 기능 개발은 TDD 방법론을 따릅니다. 이는 코드의 품질과 신뢰성을 높이고, 예측 가능한 방식으로 기능을 확장하는 데 도움을 줍니다.

- **TDD Workflow**:
    1.  **Red (실패하는 테스트 작성)**: 구현하려는 새로운 기능에 대해 실패하는 단위 테스트 또는 통합 테스트를 먼저 작성합니다.
    2.  **Green (테스트 통과)**: 최소한의 코드를 작성하여 해당 테스트를 통과시킵니다.
    3.  **Refactor (코드 리팩토링)**: 테스트가 통과했다면, 코드의 가독성, 유지보수성, 효율성을 개선하기 위해 리팩토링을 수행합니다. 이때 테스트는 리팩토링 과정에서 기능이 손상되지 않음을 보장하는 안전망 역할을 합니다.

- **테스트 디렉토리 활용**:
    - **`tests/unit/`**: 각 클래스나 함수의 가장 작은 논리적 단위가 예상대로 동작하는지 검증하는 테스트를 작성합니다. 외부 의존성(Kafka, DB 등)은 Mocking 처리합니다.
    - **`tests/integration/`**: 여러 컴포넌트가 함께 작동하여 큰 그림의 기능이 올바르게 수행되는지 확인하는 테스트를 작성합니다. 실제 Kafka 브로커와의 연동 테스트 등이 포함될 수 있습니다.

## 5. 다음 진행 계획

`prd_dev.md` 기반의 3계층 아키텍처와 TDD 전략, 그리고 Observability 설계를 반영하여
다음과 같은 단계적 개발 계획을 수립합니다.

### 2026-02-14 – 벤치마크 밸리데이션 & 리셋 준비
- `uv run pytest` 결과: 단위/통합 테스트는 통과했으나 `tests/e2e/test_ordering.py` 내 4개 시나리오가 실패했습니다. 기존 토픽(`e2e_ordering_test_topic`)이 이전 메시지를 유지하여 순서/카운트가 어긋나는 상태입니다. 토픽/컨슈머 그룹 리셋 기능으로 재시도 예정입니다.
- `pre-commit run --all-files` 결과: `pretty-format-toml` 훅이 `pkg_resources` 미탑재로 중단되어 훅 전용 venv의 `setuptools` 버전을 69.5.1로 낮춰 해결했습니다. 여전히 `tests/unit/execution_plane/test_base_execution_engine.py`의 mypy 경고는 기존 테스트 더블 제한으로 남아 있습니다.
- `benchmarks/kafka_admin.py` + `tests/unit/benchmarks/test_kafka_admin.py`를 추가하여 AdminClient 기반 리셋 헬퍼를 구현했습니다. Unknown topic/group 오류는 무시하고 나머지는 재시도 후 예외를 상승시킵니다. 관련 단위 테스트는 green입니다.
- `benchmarks/run_parallel_benchmark.py`가 기본으로 토픽/컨슈머 그룹을 삭제 후 재생성하며, `--skip-reset` 플래그로 비활성화할 수 있습니다. `README.md` 벤치마크 섹션에 해당 행동을 문서화했고, `benchmarks/pyrparallel_consumer_test.py`는 수동 실행 시 동일 헬퍼를 켤 수 있는 `reset_topic` 옵션을 노출합니다.
- 장시간 워커 부하를 실험할 수 있도록 `run_parallel_benchmark.py`에 `--timeout-sec` CLI 옵션을 추가해 async/process 라운드의 타임아웃을 조정할 수 있게 했습니다. 해당 옵션을 README에 문서화했습니다.
- 전체 `uv run pytest`는 `tests/e2e/test_ordering.py` 네 케이스가 여전히 기존 토픽 잔존 메시지로 실패(10k 메시지 요청 대비 11k 처리)했으며, 나머지 86개 테스트는 통과했습니다.
- `pre-commit run --all-files`는 기존 mypy 경고(테스트 더블 시그니처 불일치)만 남고 전부 green입니다.
- `uv run python benchmarks/run_parallel_benchmark.py --bootstrap-servers localhost:9092 --num-messages 2000 --num-keys 50 --num-partitions 4`를 실행해 baseline/async/process 라운드를 모두 성공적으로 완료했습니다. 결과 JSON은 `benchmarks/results/20260214T053950Z.json`에 저장되었습니다.
- **ProcessExecutionEngine 종료 hang 해결 (2026-02-14)**: `pyrparallel_consumer_test.py` `finally` 블록에 `await engine.shutdown()`을 추가해 워커 종료용 sentinel을 전송하도록 수정. `uv run python benchmarks/run_parallel_benchmark.py --num-messages 1000 --num-keys 10 --num-partitions 4 --skip-baseline --skip-async --bootstrap-servers localhost:9092 --topic-prefix pyrallel-benchmark-ci --process-group process-benchmark-group-ci` 실행 시 Process 라운드가 정상 종료되고 프로세스가 자동 종료됨을 확인(결과 JSON: `benchmarks/results/20260214T071451Z.json`).

현재 `BrokerPoller`의 핵심 기능 구현은 완료되었으나, 통합 테스트 단계에서 난관에 봉착했습니다. 따라서 다음 계획은 테스트를 통과시키는 데 집중합니다.

1.  **`test_run_consumer_loop_basic_flow` 통합 테스트 디버깅 및 수정 (완료)**
2.  **Backpressure 로직 테스트 작성 (대기 중)**
    - `_check_backpressure` 메서드가 부하량에 따라 `consumer.pause`와 `consumer.resume`을 올바르게 호출하는지 검증하는 테스트를 추가합니다.
3.  **모든 변경사항 커밋 (완료)**

### 5.0 진행 요약
- **완료**: Phase 1~5와 컨트롤 플레인/워크 매니저/실행 엔진 계약 등 핵심 아키텍처 구현을 끝내고, `test_run_consumer_loop_basic_flow` 통합 테스트도 디버깅을 마쳤습니다.
- **진행 중**: Observable metrics export / 운영 가이드 작성 및 통해 Observability 단계 보완, E2E 테스트(문서화 포함) 추가.
- **향후 우선순위**: Observability 문서화, 커밋 정확성 관련 E2E 검증, 단계별 인수인계와 커밋을 병행하면서 새로운 기능(Phase 6+)로 확장합니다.

---

### 5.1 Phase 1 – Control Plane Core (난이도: ★★★★) - **완료**

- **BrokerPoller** - **완료**
  - Kafka poll / pause / resume 제어 - **완료**
  - Backpressure 연계 (Load 기반 pause/resume) - **완료 (Hysteresis 검증 포함)**
  - Rebalance callback wiring - **완료**
  - Hydration (상태 복원) - **완료**

- **Rebalance & Epoch Fencing** - **완료**
  - Partition epoch 상태 머신 구현 - **완료**
  - revoke 중 completion 무시 로직 - **완료**
  - final commit + metadata 전달 - **완료**

- **OffsetTracker (State Machine)** - **완료**
- **MetadataEncoder** - **완료**
- **WorkManager** - **완료**

> TDD 우선순위: **ExecutionEngine Contract Test** → AsyncExecutionEngine → ProcessExecutionEngine

---

### 5.2 Phase 2 – WorkManager & Scheduling (난이도: ★★★★) - **완료**

병렬 처리의 **공정성 + 관측 가능성**을 책임지는 계층입니다.

- **WorkManager** - 완료
  - Virtual Partition 관리 - 완료
  - Blocking Offset 우선 스케줄링 알고리즘 - 완료
  - ExecutionEngine submit 제어 - 완료

- **Scheduling Policy** - 완료
  - Lowest blocking offset 우선 - 완료
  - starvation 방지 - (부분적으로 해결, 개선 필요)
  - rebalance 중 submit 차단 - 완료

- **Observability Integration** - 완료
  - Blocking Offset duration 추적 - 완료
  - Gap / True Lag 계산 노출 - 완료
  - Backpressure 판단 지표 제공 - 완료

> 이 단계까지 완료되면 Mock ExecutionEngine으로 end-to-end 테스트 가능

---

### 5.3 Phase 3 – Execution Abstraction (난이도: ★★★) - **완료**

Execution Plane의 계약을 고정하는 단계입니다.

- **ExecutionEngine 인터페이스** - 완료
  - submit()
  - shutdown()
  - metrics()

- **DTO 정의** - 완료
 - CompletionEvent
 - EngineMetrics

### 5.4 Retry + DLQ 설계 (2026-02-16)
- `docs/plans/2026-02-16-retry-dlq-design.md`에 재시도 + DLQ 설계를 기록했습니다. 실행 엔진 내부 재시도(기본 3회, 지수 백오프 1s 시작, 최대 30s, 지터 200ms) 후 실패 시 BrokerPoller가 DLQ로 발행하고 성공 시에만 커밋하도록 합니다.
- `ExecutionConfig`에 `max_retries`, `retry_backoff_ms`, `exponential_backoff`, `max_retry_backoff_ms`, `retry_jitter_ms`를 추가하고, `KafkaConfig`에 `dlq_enabled`를 추가하여 기존 `dlq_topic_suffix`를 실제 사용합니다.
- DLQ 발행 시 원본 key/value를 보존하고, 헤더에 `x-error-reason`, `x-retry-attempt`, `source-topic`, `partition`, `offset`, `epoch`를 포함합니다. DLQ 발행 실패 시 재시도하며 성공 전에는 커밋하지 않습니다.

### 5.5 Retry + DLQ 구현 (2026-02-17)
- Async/Process 엔진에 재시도 및 백오프 구현: `max_retries`, `retry_backoff_ms`, `exponential_backoff`, `max_retry_backoff_ms`, `retry_jitter_ms` 적용. `CompletionEvent.attempt`로 1-based 시도 횟수 노출.
- BrokerPoller: 실패 이벤트가 최대 재시도에 도달하면 DLQ로 발행 후 성공 시에만 커밋. 실패 시 커밋 스킵. 메시지 key/value는 소비 시 캐싱 후 사용, 헤더에 에러/시도/소스 정보를 포함. DLQ 비활성 시 기존 커밋 흐름 유지.
- 문서: README 재시도/DLQ 옵션 추가, prd_dev 설정 스키마에 재시도/DLQ 옵션 명시, 계획/설계 문서 (`docs/plans/2026-02-16-retry-dlq-plan.md`, `docs/plans/2026-02-16-retry-dlq-design.md`) 작성.
- 테스트: `tests/unit/control_plane/test_broker_poller_dlq.py` 추가, 재시도/백오프/커밋 조건을 모킹으로 검증. Async/Process 엔진 재시도 테스트 확장. 전체 `pytest` 실행 시 e2e Kafka가 없어서 `tests/e2e/test_ordering.py`는 부트스트랩 연결 실패로 오류(로컬 Kafka 미기동). 나머지 단위/통합 테스트는 통과. `pre-commit run --all-files`는 모두 통과.
- 추가 안정화(2026-02-17): ProcessExecutionEngine in-flight 카운터에 락을 추가해 경합을 방지. DLQ 발행 실패 시 캐시를 보존하고 커밋을 스킵하도록 조정. DLQ flush 타임아웃을 `KafkaConfig.DLQ_FLUSH_TIMEOUT_MS`(기본 5000ms)로 설정화. 타이밍 민감 테스트에 여유 허용치(0.9x) 적용. 단위/통합 테스트 139개 통과; e2e는 로컬 Kafka 미기동으로 미수행.

### 5.6 성능 벤치/프로파일 (2026-02-18)
- 성능 기준
  - Async 엔진 100k: 31.25s, TPS≈3,199, avg≈0.61s, p99≈0.72s (`benchmarks/results/20260218T103057Z.json`).
  - Process 엔진 100k (갭 캐싱 후): 96.60s, TPS≈1,035, p99≈1.98s (`benchmarks/results/20260218T102444Z.json`).
- 프로파일(20k, process, yappi): 완료 74.15s, TPS≈270, p99≈2.67s. 주요 핫스팟 ttot:
  - WorkManager.schedule 48.99s
  - WorkManager.poll_completed_events 47.42s
  - WorkManager.get_blocking_offsets 37.17s
  - OffsetTracker.get_gaps 32.95s
  - Logging(Logger.debug/_log/StreamHandler) 합산 ~60s
  → 컨트롤 플레인 gaps/블로킹 계산 + 로깅 오버헤드가 지배적.
- 개선 조치
  - OffsetTracker gaps 캐싱 및 동일 갭 반복 5000회마다 WARN 추가.
  - BrokerPoller 블로킹 오프셋 5초 초과 WARN 추가.
  - WorkManager 디버그 스팸 제거.
- 남은 튜닝 방향
  - gaps/blocking 계산 호출 빈도 축소 또는 변경 발생 시에만 계산.
  - DEBUG 로깅 샘플링/축소로 오버헤드 완화.
  - 추가 프로파일(100k) 시 gaps 반복 루프가 재발하면 반복 패턴 스로틀/스킵 검토.
  - TopicPartition
  - TaskContext (epoch 포함)

- **Engine Factory** - 완료
  - 설정 기반 엔진 선택
  - async / process 공존 구조 확정

- **Contract Test Suite** - 완료
  - ExecutionEngine 공통 동작 검증
  - observability 항목 포함

---

### 5.4 Phase 4 – AsyncExecutionEngine (난이도: ★★★) - **완료**

Python asyncio 환경에 최적화된 기본 실행 모델입니다.

- **Task Pool** - 완료
  - asyncio.Task 기반 실행
  - Semaphore 기반 max_in_flight 제어

- **Completion Channel** - 완료
  - asyncio.Queue 기반 completion 전달
  - epoch 포함 completion event 생성

- **Async 전용 테스트** - 완료
  - high concurrency 시나리오
  - pause 상태에서도 completion 처리 검증

---

### 5.5 Phase 5 – ProcessExecutionEngine (난이도: ★★★★★) - **완료**

GIL 회피를 위한 고난이도 실행 모델입니다. `ProcessExecutionEngine`의 성공적인 구현 및 테스트를 완료했습니다.

- **IPC 채널**
  - multiprocessing.Queue 기반 task / completion 통신 - 완료
  - worker_loop 구현 - 완료

- **Worker Process 관리**
  - crash 감지 및 worker 재기동
  - sentinel 기반 graceful shutdown - 완료

- **Process 전용 테스트**
  - worker crash 복구
  - partial completion + epoch fencing
  - shutdown 중 completion drain 검증 - 완료

---

### 5.6 Observability & 운영 품질 (난이도: ★★★) - **진행 중**

라이브러리 신뢰성을 외부에 드러내는 단계입니다.

- **Metrics Export Layer** - **완료**
  - True Lag, Gap, Blocking Offset (Top N), In-flight / Capacity 지표 수집 및 DTO 정의 완료 (`SystemMetrics`, `PartitionMetrics`)
  - `BrokerPoller.get_metrics()` 구현 완료
- **Dashboard Spec** - 대기 중
  - Grafana 패널 설계
  - 장애 시나리오 기반 뷰 구성
- **운영 가이드** - 대기 중
  - Kafka Lag vs True Lag 설명
  - Blocking Offset 대응 전략

---

### 5.7 권장 개발 순서 (TDD 기준)

1. OffsetTracker + Observability API - 완료
2. Rebalance & Epoch Fencing - 완료
3. WorkManager + Scheduling - 완료
4.  **BrokerPoller 기능 보완 및 테스트 작성** - **완료**
5. ExecutionEngine Contract Test - **완료**
6. AsyncExecutionEngine 구현 - **완료**
7. ProcessExecutionEngine 구현 - **완료**
8. Observability Export & Docs - **진행 중**

### 5.8 E2E 테스트 구현 (2026-02-10)

`tests/e2e/test_ordering.py`에 전체 시스템의 E2E 테스트를 구현했습니다. 실제 Kafka 브로커와 `benchmarks/producer.py`를 사용하여 메시지를 생성하고, `BrokerPoller` → `WorkManager` → `AsyncExecutionEngine` 전체 파이프라인을 검증합니다.

#### 테스트 인프라
- **`ResultTracker`**: 키별(`results`) 및 파티션별(`partition_results`) 처리 순서를 기록하고 검증하는 헬퍼 클래스
- **`run_ordering_test()`**: 공통 테스트 설정(BrokerPoller, WorkManager, Engine 생성, producer 실행, stop_event 대기)을 캡슐화한 헬퍼 함수. `worker_fn`, `max_in_flight`, `timeout` 파라미터 지원
- **`create_e2e_topic` fixture**: 테스트 전후 토픽(`e2e_ordering_test_topic`, 8 파티션) 생성/삭제

#### 구현된 테스트 (5개)
1. **`test_key_hash_ordering`**: KEY_HASH 모드에서 동일 키 내 sequence 오름차순 보장 검증 (10000 msgs, 100 keys)
2. **`test_partition_ordering`**: PARTITION 모드에서 동일 파티션 내 오프셋 오름차순 보장 검증 (10000 msgs, 100 keys)
3. **`test_unordered`**: UNORDERED 모드에서 전체 메시지 처리 완료 검증 (10000 msgs, 100 keys)
4. **`test_backpressure`**: `max_in_flight=20`으로 제한된 상태에서 500개 메시지 처리 완료 및 `MAX_IN_FLIGHT_MESSAGES`, `MIN_IN_FLIGHT_MESSAGES_TO_RESUME` 설정값 검증. 인라인 구성 사용
5. **`test_offset_commit_correctness`**: 랜덤 지연 워커로 500개 메시지 처리 후, Kafka에 커밋된 오프셋이 실제 처리된 최대 오프셋+1을 초과하지 않는지 검증. 인라인 구성 사용

#### 주요 버그 수정
- `benchmarks/producer.py` 호출 시 `--topic` 인자 누락 수정 (기본값 `test_topic` 대신 `e2e_ordering_test_topic` 사용)
- `test_offset_commit_correctness`의 `stop_event` 접근 불가 버그 수정: 커스텀 `worker_fn`을 `run_ordering_test()`에 전달할 경우 내부 `stop_event`에 접근할 수 없어 항상 타임아웃되던 문제를 인라인 구성으로 리팩토링하여 해결

### 5.9 운영 안정성 개선 (2026-02-10)

프로세스 기반 실행 엔진의 운영 안정성을 높이기 위해 3건의 개선을 수행했습니다.

#### 5.9.1. `multiprocessing.Value` → `int` 단순화 (완료)
- **문제**: `ProcessExecutionEngine`의 `_in_flight_count`가 `multiprocessing.Value("i", 0)`로 구현되어 Lock 오버헤드 발생. 그러나 이 카운터는 메인 프로세스의 async 이벤트 루프에서만 접근하므로 프로세스 간 공유가 불필요.
- **수정**: 일반 `int`로 교체. `Value` import 제거, Lock 획득 코드 제거.
- **파일**: `process_engine.py`

#### 5.9.2. `poll_completed_events` 무한 드레인 방지 (완료)
- **문제**: `poll_completed_events`가 큐가 빌 때까지 무한 루프로 이벤트를 꺼내, 대량 완료 시 이벤트 루프를 장시간 블로킹할 수 있음.
- **수정**: `batch_limit: int = 1000` 파라미터 추가. `BaseExecutionEngine`, `ProcessExecutionEngine`, `AsyncExecutionEngine` 모두 동일하게 적용.
- **파일**: `base.py`, `process_engine.py`, `async_engine.py`

#### 5.9.3. 멀티프로세스 QueueHandler/QueueListener 로깅 (완료)
- **문제**: 워커 프로세스가 부모 프로세스의 로거를 그대로 상속받아, 여러 프로세스가 동시에 같은 핸들러에 쓰면 로그 출력이 뒤섞이거나 깨질 수 있음.
- **수정**:
  - `logger.py`에 `LogManager.setup_worker_logging(log_queue)`, `LogManager.create_queue_listener(log_queue, handlers)` 유틸리티 추가.
  - `ProcessExecutionEngine.__init__`에서 `log_queue` 생성 및 `QueueListener` 시작.
  - `_worker_loop`에 `log_queue` 파라미터 추가, 진입 시 `setup_worker_logging()` 호출.
  - `shutdown()`에서 `QueueListener.stop()` 호출.
- **파일**: `logger.py`, `process_engine.py`

#### 테스트 결과
- 82개 테스트 통과 (unit 80 + integration 2), 0 failures

### 5.10 재시도 및 DLQ (Dead Letter Queue) 구현 (2026-02-17)

실패한 메시지에 대한 자동 재시도와 최종 실패 시 DLQ 퍼블리싱 기능을 완전히 구현했습니다.

#### 5.10.1. 구성 필드 추가 (Task 1 완료)
- **ExecutionConfig**: `max_retries=3`, `retry_backoff_ms=1000`, `exponential_backoff=True`, `max_retry_backoff_ms=30000`, `retry_jitter_ms=200` 추가
- **KafkaConfig**: `dlq_enabled=True`, `dlq_topic_suffix='.dlq'` 추가
- **파일**: `config.py`, `tests/unit/test_config.py`
- **테스트**: 재시도/DLQ 설정 기본값 및 오버라이드 검증, `dump_to_rdkafka` 제외 검증

#### 5.10.2. CompletionEvent attempt 필드 추가 (Task 2 완료)
- **DTO**: `CompletionEvent`에 `attempt: int` 필드 추가하여 재시도 횟수 추적
- **파일**: `dto.py`
- **테스트**: AsyncExecutionEngine, ProcessExecutionEngine의 모든 테스트에서 attempt 필드 검증

#### 5.10.3. AsyncExecutionEngine 재시도 로직 (Task 3 완료)
- **구현**:
  - 워커 호출을 재시도 루프로 래핑
  - 지수/선형 백오프 계산 (cap + jitter)
  - 타임아웃도 재시도 대상으로 처리
  - 세마포어는 재시도 간 유지 (최종 완료 시에만 release)
- **파일**: `async_engine.py`, `tests/unit/execution_plane/test_async_execution_engine.py`
- **테스트**: 첫 시도 성공, 재시도 후 성공, 최종 실패, 타임아웃 재시도, 백오프 타이밍, 백오프 cap 검증

#### 5.10.4. ProcessExecutionEngine 재시도 로직 (Task 4 완료)
- **구현**:
  - 워커 프로세스 내에서 재시도 수행
  - 배치 처리 시 아이템별 독립적 재시도
  - 동일한 백오프 계산 로직 적용
  - in-flight 카운트는 아이템별로 최종 완료 시에만 감소
- **파일**: `process_engine.py`, `tests/unit/execution_plane/test_process_engine_batching.py`
- **테스트**: 재시도 후 성공, 최종 실패, 백오프 타이밍, 백오프 cap, 즉시 성공 시 attempt=1 검증

#### 5.10.5. BrokerPoller DLQ 퍼블리싱 (Task 5 완료)
- **구현**:
  - `_message_cache: Dict[Tuple[DtoTopicPartition, int], Tuple[Any, Any]]`로 메시지 key/value 보존
  - `_publish_to_dlq` 헬퍼 메서드: DLQ 토픽으로 퍼블리싱 + 재시도 로직 + 헤더 추가
  - 실패 이벤트 처리 시 `attempt >= max_retries` 검증 후 DLQ 퍼블리싱
  - DLQ 퍼블리싱 실패 시 오프셋 커밋 건너뜀 (gap 유지)
  - `dlq_enabled=False`일 때는 기존 동작 유지 (로깅만, 정상 커밋)
- **헤더**: `x-error-reason`, `x-retry-attempt`, `source-topic`, `partition`, `offset`, `epoch`
- **파일**: `broker_poller.py`, `tests/integration/test_broker_poller_integration.py`
- **테스트**: DLQ 퍼블리싱 성공 시 커밋, 비활성화 시 건너뛰기, 재시도 실패 시 커밋 건너뛰기 검증

#### 5.10.6. 와이어링 검증 (Task 6 완료)
- **확인**: `engine_factory.py`가 전체 `config` 객체를 양쪽 엔진에 전달하여 자동 와이어링 확인
- **테스트**: 전체 실행 엔진 + 통합 테스트 재실행 (45개 통과)

#### 5.10.7. 문서 업데이트 (Task 7 완료)
- **파일**: `README.md`
- **추가 섹션**: "재시도 및 DLQ 설정" (환경 변수, 백오프 계산, 헤더 형식, 동작 흐름, 예제 코드)

#### 5.10.8. 최종 검증 (Task 8 완료)
- **pre-commit**: 전체 hooks 통과 (mypy 포함)
- **mypy 수정**: `test_base_execution_engine.py`의 `poll_completed_events` 시그니처 수정 (batch_limit 파라미터 추가)
- **로깅 검증**: f-string 없음 확인 ✓
- **assert 검증**: 프로덕션 코드에 assert 없음 확인 ✓
- **전체 테스트**: 138개 통과 (unit 123 + integration 5), 0 failures

#### 구현 상세
- **메시지 캐싱**: `(TopicPartition, offset)` 튜플을 키로 사용해 key/value 보존
- **DLQ 재시도**: `asyncio.to_thread`로 동기 producer 연산 래핑, 동일한 백오프 설정 재사용
- **에포크 펜싱**: DLQ 헤더에 epoch 포함하여 리밸런싱 추적 가능
- **LSP 타입 이슈**: confluent-kafka의 `metadata` kwarg는 런타임 동작하나 타입 스텁 누락 (무시 가능)

#### 테스트 결과
- 138개 테스트 통과 (unit 128 + integration 5), 0 failures
- 3개 경고 (unawaited mock coroutines, non-critical)
- pre-commit 전체 hooks 통과

### 5.11 Yappi 프로파일링 계획 수립 (2026-02-24)

- `docs/plans/2026-02-24-profile-analysis-design.md`에 baseline/async/process 벤치마크를 yappi로 프로파일링하고 snakeviz로 시각화하는 절차(풀볼륨/스모크/오프라인 분석 옵션 포함)를 정리했습니다.
- `benchmarks/results/profiles/` 경로에 `.prof` 산출물이 아직 없으며, 실행에는 로컬 Kafka 클러스터가 필요합니다.
- 다음 단계: Kafka를 띄운 뒤 `uv run python -m benchmarks.profile_benchmark_yappi --bootstrap-servers localhost:9092 --modes baseline async process`(메시지/키/파티션 수는 용량에 맞게 조정)로 프로파일 생성 → pstats/console 요약과 snakeviz로 병목 비교.

### 5.12 Yappi 프로파일 실행 및 병목 관찰 (2026-02-24)

- 실행: `uv run python -m benchmarks.profile_benchmark_yappi` (10k msgs, 100 keys, 8 partitions, timeout 120s).
- 산출물: `benchmarks/results/profiles/20260224T091356Z/` 내 `baseline_profile.prof`, `async_profile.prof`, `process_profile.prof`.
- 요약 (실측 TPS/런타임): baseline 0.23s / 44,364 TPS; async 4.33s / 2,309 TPS; process 8.60s / 1,163 TPS.
- Hotspot (async): yappi 누적 대부분이 `asyncio.tasks.sleep`(10,010 calls, ~1,498 cum sec)와 `asyncio.wait_for` → 벤치마크 워커의 5ms 슬립이 전체 병목. 실제 런타임 4.33s로 합산된 wall-time은 동시성 합.
- Hotspot (process): 누적 상위에 `asyncio.sleep`(diag/worker), `Queue.get`(~8.5s), `BrokerPoller._run_consumer`(~8.5s); 프로파일은 메인 프로세스만 캡처(워커 프로세스 yappi 미수집).
- 개선 아이디어: (1) 벤치마크 워커 슬립을 파라미터화/기본 축소하여 엔진 오버헤드만 측정, (2) 프로세스 워커에서 yappi 시작/저장하도록 래핑해 실제 워커 호출 스택 수집, (3) 프로파일 시 diag 루프(5s sleep) 비활성화 옵션 추가로 노이즈 축소.

### 5.13 프로파일러 정합성 개선 및 재실행 (2026-02-24)

- 변경: baseline/async/process 모두 동일한 워크로드를 사용하도록 벤치마크 정렬.
  - `profile_benchmark_yappi.py`에 `--worker-sleep-ms` 추가(기본 5ms), 공통 슬립 워커를 baseline/async/process에 적용.
  - `run_pyrallel_consumer_test`는 커스텀 워커 주입(비동기/프로세스) 허용, baseline 소비자도 커스텀 worker_fn을 받아 동일 워크로드 실행.
- 재실행: `uv run python -m benchmarks.profile_benchmark_yappi --worker-sleep-ms 5` 산출물 `benchmarks/results/profiles/20260224T111936Z/`.
- 실측 TPS/런타임(10k msgs, 5ms work): baseline 61.8s / 161.7 TPS; async 4.19s / 2,387 TPS; process 8.59s / 1,163 TPS.
- Hotspot 정합: 이제 세 모드 모두 동일 슬립이 상단에 나타남 (`_sleep_work_*` / `asyncio.sleep`), 병렬 처리 이점(특히 async) 명확하게 비교 가능. Process 프로파일은 여전히 메인 프로세스 중심(워커별 yappi 미포함)으로 표시되므로 워커 프로파일링 필요 시 추가 수집 필요.

### 5.14 벤치마크/프로파일 통합 플래그 및 워크로드 선택 추가 (2026-02-24)

- `benchmarks/run_parallel_benchmark.py`에 프로파일 토글/출력 옵션 추가: `--profile`, `--profile-dir`, `--profile-clock`, `--profile-top-n`, `--profile-threads`, `--profile-greenlets`(yappi 필요, 기본 off).
- 동일 스크립트에 워크로드 선택 추가: `--workload {sleep,cpu,io}` + `--worker-sleep-ms`/`--worker-cpu-iterations`/`--worker-io-sleep-ms`를 baseline/async/process 공통으로 주입(커스텀 워커 훅 사용).
- 프로파일 .prof는 모드명으로 `profile_dir/run_name.prof` 저장, top-N 출력 옵션 지원. 프로세스 모드 워커 내부 yappi는 아직 미적용(필요 시 후속 작업).

### 5.15 프로세스 워커 프로파일링 및 벤치마크 README 추가 (2026-02-24)

- `run_parallel_benchmark.py`: 프로파일 모드에서 프로세스 워커 내부에서도 yappi를 시작하고 종료 시 per-worker `.prof`를 `run_name-worker-<pid>.prof`로 저장하도록 래핑(프로파일 실패 시 워커 진행 유지). `datetime.utcnow()` 사용을 UTC aware `datetime.now(datetime.UTC)`로 교체.
- 워크로드 옵션에 `all` 추가(sleep→cpu→io 순차 실행, 토픽/그룹 접미사로 충돌 방지). run_name에 워크로드 접두사 부여해 결과/프로파일 구분.
- `benchmarks/README.md`에 사용법/옵션 업데이트.

### 5.16 consumer/offset_manager 커버리지 보강 (2026-02-25)

- `tests/unit/test_consumer_and_offset_manager.py` 추가: PyrallelConsumer wiring(start/stop/metrics) 더미 객체로 검증, OffsetTracker add/remove/safe_offsets/total_in_flight 테스트로 커버리지 확보.
- 루트 `README.md`에 프로파일 OFF 벤치마크 샘플(TPS) 표 추가 (sleep/cpu/io workload, 4 partitions, 2000 msgs, 100 keys).

### 5.17 BrokerPoller 견고성 및 E2E 정렬 테스트 고정 (2026-02-25)

- `broker_poller`: mock 친화적으로 기본 numeric 값 사용(poll batch/worker size, blocking_warn_seconds/diag_log_every)하고, partition ordering 시 submit key를 파티션 ID로 고정해 PARTITION 모드 정렬 보장. commit 시 KafkaTopicPartition metadata를 설정해 통합 테스트 기대 충족.
- E2E ordering 테스트 속도 단축(대량 메시지 2000으로 감소) 및 PARTITION 모드 정렬 실패 수정.
- 통합 테스트(`tests/integration`)와 E2E ordering 전체 통과 확인.

### 5.18 README 시작 가이드 추가 (2026-02-25)

- 루트 `README.md`에 설치/설정/워커 정의( async I/O, CPU, sleep ), 실행 엔진 선택 예시를 포함한 빠른 시작 섹션을 추가했습니다.

### 5.19 ExecutionMode Enum 도입 (2026-02-25)

- `pyrallel_consumer.dto.ExecutionMode` 추가, `ExecutionConfig.mode`를 Enum으로 전환하고 `engine_factory`에서 문자열 입력 시 Enum으로 정상 변환하도록 처리.
- README 예제를 `ExecutionMode.ASYNC/PROCESS`로 갱신. 주요 유닛/통합 테스트 재실행(통과, 기존 경고만 유지).

### 5.20 IPC 직렬화 msgpack 전환 및 모니터링 스택 확장 (2026-02-25)

- `process_engine`: multiprocessing 큐에서 pickle을 제거하고 WorkItem/CompletionEvent를 msgpack으로 직렬화/역직렬화하도록 변경. 헬퍼 추가, 배치 버퍼 플러시 시 msgpack bytes 전송, 워커/메인 모두 디코딩 후 처리. `pyproject.toml`에 `msgpack` 의존성 추가.
- `docker-compose.yml`에 Prometheus(9090), Grafana(3000), Kafka Exporter(9308) 추가. `monitoring/prometheus.yml` 작성.
- README 모니터링 가이드 추가(메트릭 활성화, compose up, Grafana 데이터소스). 사용법 섹션 재정리.
- `.env.sample` 추가: Kafka/Parallel Consumer/Execution/Metrics/DLQ 설정 예시 포함.

### 5.21 Backpressure 큐 한도 추가 (2026-02-25)

- `ParallelConsumerConfig.queue_max_messages` 기본 5000 도입, BrokerPoller에서 총 대기 메시지가 한도 초과 시 pause, 70% 이하로 줄면 resume(기존 in-flight 기반 히스테리시스와 병행).
- `.env.sample`/README 예시에 queue_max_messages 추가.

### 5.22 ProcessEngine in-flight 레지스트리 확장 및 커밋 클램프 (2026-02-25)

- ProcessExecutionEngine: Manager 기반 in-flight 레지스트리를 워커별 리스트로 확장하여 워커가 잡은 다중 작업을 추적. 워커 사망 시 리스트의 모든 작업을 msgpack으로 재큐잉 후 워커 재시작. 최소 in-flight 오프셋 조회가 파티션별로 다중 항목을 고려하도록 변경.
- BrokerPoller: 커밋 계산 시 레지스트리 최소 오프셋을 커밋 상한으로 적용해 더 안전한 커밋 지점 확보.
- 단위 회귀: clamp 테스트 추가(`test_broker_poller_inflight_clamp`), 프로세스 엔진 관련 빠른 회귀 통과.

### 5.15 프로세스 워커 프로파일링 및 벤치마크 README 추가 (2026-02-24)

- `run_parallel_benchmark.py`: 프로파일 모드에서 프로세스 워커 내부에서도 yappi를 시작하고 종료 시 per-worker `.prof`를 `run_name-worker-<pid>.prof`로 저장하도록 래핑(프로파일 실패 시 워커 진행 유지).
- 동일 파일에 프로파일/워크로드 옵션 문서화용 README 추가: `benchmarks/README.md`에 사용 예시, 옵션 요약, 출력 위치 설명.
