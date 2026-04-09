# Security Policy

## Supported Versions

현재는 prerelease/alpha 계열만 공개되어 있으므로, 최신 공개 버전만 보안 수정 대상으로 간주합니다.

| Version | Supported |
| --- | --- |
| latest prerelease | Yes |
| older prerelease builds | Best effort |

stable release가 시작되면 지원 매트릭스를 이 문서에서 명시적으로 확장합니다.

## Reporting A Vulnerability

보안 이슈는 공개 GitHub issue로 올리지 말고, 저장소 관리자에게 비공개 채널로 먼저 제보해 주세요.

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
