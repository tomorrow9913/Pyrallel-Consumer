# Async Execution Engine Design

## 1. 문서 역할

이 문서는 async engine이 외부에 노출하는 구현 직전 계약을 고정한다.
worker type validation이나 timeout 정책을 수정할 때 먼저 읽는 문서다.

## 2. 핵심 설정 키

| 키 | 의미 | 기본값 |
| --- | --- | --- |
| `EXECUTION_MODE` | 실행 모드 | `async` |
| `EXECUTION_MAX_IN_FLIGHT` | 전체 async in-flight 제한 | `1000` |
| `EXECUTION_MAX_RETRIES` | 최대 재시도 횟수 | `3` |
| `EXECUTION_RETRY_BACKOFF_MS` | 기본 backoff | `1000` |
| `EXECUTION_ASYNC_CONFIG__TASK_TIMEOUT_MS` | task timeout | `30000` |
| `EXECUTION_ASYNC_CONFIG__SHUTDOWN_GRACE_TIMEOUT_MS` | shutdown grace | `5000` |

## 3. worker 계약

| 항목 | 계약 |
| --- | --- |
| worker type | `async def worker(item: WorkItem)` |
| 입력 | `WorkItem` 1건 |
| 성공 | 예외 없이 완료 |
| 실패 | 예외 발생 또는 timeout |

## 4. completion 계약

`CompletionEvent`는 최소 아래 의미를 가져야 한다.

| 필드 | 의미 |
| --- | --- |
| `id` | 원본 `WorkItem.id` |
| `tp` | topic/partition |
| `offset` | 처리 대상 offset |
| `epoch` | 제출 시점 partition epoch |
| `status` | `success` 또는 `failure` |
| `error` | 실패 이유 문자열 |
| `attempt` | 최종 시도 횟수 |

## 5. shutdown 규칙

- 새 `submit()`은 shutdown 시작 이후 거부된다.
- grace timeout 안에 끝난 task는 자연 종료를 허용한다.
- grace timeout 이후 남은 task는 cancel 한다.
- cancel 이후에도 completion queue와 task registry는 leak 없이 정리돼야 한다.
