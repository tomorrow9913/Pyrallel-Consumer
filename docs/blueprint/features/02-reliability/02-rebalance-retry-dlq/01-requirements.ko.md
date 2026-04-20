# Rebalance Retry DLQ Requirements

## 1. 문서 목적

이 문서는 `rebalance-retry-dlq`의 책임과 요구사항을 정의한다.
이 subfeature는 partition ownership change와 worker failure를 offset-loss 없이 처리하는 보호 계층이다.

## 2. 책임

- assignment/revoke 시 partition epoch를 관리해야 한다.
- stale completion은 현재 epoch과 다르면 폐기해야 한다.
- revoke 시점에는 새 submit을 멈추고 제한 시간 안에서 마지막 graceful commit을 시도해야 한다.
- worker failure는 retry/backoff 후 최종 실패만 DLQ 경로로 보내야 한다.
- DLQ publish 실패 시에는 commit을 전진시키지 않아야 한다.

## 3. 기능 요구사항

- assignment 시 committed offset과 optional metadata snapshot을 사용해 상태를 복원할 수 있어야 한다.
- revoke 시 `max_revoke_grace_ms`를 넘기면 일부 sparse state를 포기하고 liveness를 우선할 수 있어야 한다.
- async/process 두 엔진 모두 최대 재시도 횟수와 backoff 정책을 공유해야 한다.
- DLQ는 topic suffix와 payload mode 설정을 따라야 한다.
- raw payload cache miss 시에는 metadata-only publish로 degrade 가능해야 한다.

## 4. 비기능 요구사항

- rebalance 안전성은 throughput 최적화보다 우선한다.
- stale completion drop은 deterministic 해야 한다.
- retry/backoff 정책은 worker type과 무관하게 일관된 surface를 가져야 한다.

## 5. 입력/출력 경계

입력:

- assignment / revoke callback
- current partition epoch
- worker completion status / error / attempt
- DLQ 설정과 raw payload cache

출력:

- final graceful commit 또는 snapshot 포기
- retry/backoff 지연
- DLQ publish payload + headers
- stale completion drop decision

## 6. MVP 경계

포함:

- epoch fencing
- revoke grace timeout
- retry/backoff
- DLQ publish와 metadata-only degrade

제외:

- external retry queue
- manual replay console
- exactly-once recovery journal

## 7. acceptance 기준

- `Correctness < Liveness`가 rebalance revoke 경계에서만 제한적으로 적용된다는 점이 문서에 명시돼야 한다.
- retry exhaustion과 DLQ publish 성공 여부가 commit 전진 조건과 연결돼야 한다.
- stale completion이 이전 partition generation 결과라는 설명이 feature 문서에 드러나야 한다.
