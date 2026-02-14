# Pyrallel Consumer Examples / 예제

This directory contains executable examples demonstrating the two core modes of Pyrallel Consumer.
이 디렉토리는 Pyrallel Consumer의 두 가지 핵심 모드를 보여주는 실행 가능한 예제 코드를 포함하고 있습니다.

## Prerequisites / 사전 요구사항

Ensure you have a Kafka broker running locally on `localhost:9092` and a topic named `example-topic`.
로컬 환경의 `localhost:9092`에 Kafka 브로커가 실행 중이어야 하며, `example-topic`이라는 토픽이 생성되어 있어야 합니다.

```bash
# Create topic / 토픽 생성
kafka-topics --create --topic example-topic --bootstrap-server localhost:9092 --partitions 4 --replication-factor 1
```

## Examples / 예제 목록

### 1. Async IO Consumer (`examples/async_simple.py`)
*   **Use Case**: I/O bound tasks like API calls, database queries, or network requests.
*   **Mechanism**: Uses Python `asyncio` to handle thousands of concurrent tasks efficiently within a single process.
*   **Key Config**: `mode="async"`, `task_timeout_ms`.
*   **사용 사례**: API 호출, 데이터베이스 쿼리, 네트워크 요청과 같은 I/O 대기 중심의 작업.
*   **작동 방식**: Python `asyncio`를 사용하여 단일 프로세스 내에서 수천 개의 동시 작업을 효율적으로 처리합니다.

### 2. CPU Bound Process Consumer (`examples/process_cpu.py`)
*   **Use Case**: CPU heavy tasks like image processing, complex calculations, or ML inference.
*   **Mechanism**: Uses `multiprocessing` to bypass the GIL (Global Interpreter Lock). Spawns separate worker processes.
*   **Key Config**: `mode="process"`, `process_count`.
*   **Note**: The worker function must be top-level and picklable.
*   **사용 사례**: 이미지 처리, 복잡한 연산, 머신러닝 추론 등 CPU 자원을 많이 사용하는 작업.
*   **작동 방식**: `multiprocessing`을 사용하여 GIL(Global Interpreter Lock) 제약을 우회하고 별도의 워커 프로세스를 생성합니다.
*   **주의**: 워커 함수는 직렬화(pickle) 가능해야 하므로 최상위 레벨에 정의되어야 합니다.

## Running / 실행 방법

```bash
# Set Python path to include the library / 라이브러리 경로 설정
export PYTHONPATH=$PYTHONPATH:.

# Run Async Example / Async 예제 실행
python examples/async_simple.py

# Run Process Example / Process 예제 실행
python examples/process_cpu.py
```
