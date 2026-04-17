# Security Policy

## Supported Versions

이 문서의 지원 기준은 `docs/operations/support-policy.md`와 동일하게 유지한다.

### 현재(prerelease-only) 단계

| Version | Supported |
| --- | --- |
| latest prerelease | Yes |
| older prerelease builds | Best effort |

### stable 런치 이후(`1.0.0`부터 적용)

| Version line | Security support |
| --- | --- |
| latest stable minor | Yes |
| previous stable minor | Security fixes only |
| prerelease builds newer than latest stable | Best effort |
| older prerelease builds | Best effort |

## Reporting A Vulnerability

보안 이슈는 공개 GitHub issue로 올리지 말고 비공개 채널로 먼저 제보해 주세요.

권장 비공개 채널:

1. GitHub 저장소 `Security` 탭의 `Report a vulnerability`
2. GitHub private security advisory draft

- 포함하면 좋은 정보
  - 영향 범위
  - 재현 절차
  - 로그 또는 스택트레이스
  - 가능한 완화 방법

- 기대 응답
  - 접수 확인: 영업일 기준 3일 이내 목표
  - 초기 triage: 영업일 기준 7일 이내 목표

## Scope Notes

- Kafka topic 이름, DLQ topic suffix, serialization payload는 입력 검증의 일부로 취급합니다.
- secrets는 저장소에 커밋하지 않고 환경 변수 또는 배포 비밀 저장소를 사용해야 합니다.
- 운영 환경에서는 `KAFKA_DLQ_PAYLOAD_MODE=metadata_only`를 우선 검토해 민감 payload 노출 범위를 줄이세요.

## Coordinated Disclosure

수정 전까지는 취약점 상세를 공개하지 않는 것을 기본 원칙으로 합니다. 수정본이 배포되면 영향 범위와 완화 내용을 changelog 또는 release note에 요약합니다.
