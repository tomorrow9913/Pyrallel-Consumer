# Support & Compatibility Policy

이 문서는 stable 운영 기준의 지원 범위와 호환성/폐기 정책을 정의한다.

## Supported Release Lines

| Release line | Support status | Notes |
| --- | --- | --- |
| `1.x` (latest stable) | Active support | security fix/bugfix 대상 |
| `0.1.xa*` prerelease | Best effort | API/behavior compatibility 미보장 |

- `1.x`는 운영 기준 primary support line이다.
- prerelease는 실험/검증 목적이며, patch 제공이나 strict SLA를 보장하지 않는다.

## Runtime Compatibility Scope

- Python: package metadata 기준 `>=3.12` (현재 classifier: `3.12`, `3.13`)
- Kafka broker/client: 프로젝트의 Docker/CI 기반 E2E 경로(`localhost:9092` 기준)를 compatibility baseline으로 간주
- Baseline 밖 조합(다른 배포판, 구버전 broker/client 조합)은 best-effort로 취급

## Security Support

- 보안 제보 채널과 응답 목표는 [`SECURITY.md`](../../SECURITY.md)를 따른다.
- 보안 수정은 원칙적으로 active stable line(`1.x`)에 우선 제공한다.
- prerelease line에 대한 보안 백포트는 case-by-case best-effort다.

## Deprecation & EOL Policy

- stable line에서 breaking change가 필요한 경우, 다음 minor/major 계획과 migration note를 `CHANGELOG.md`에 사전 공지한다.
- deprecation은 최소 한 번의 stable minor window 동안 경고/문서 안내를 유지한 후 제거를 검토한다.
- line EOL 시점에는 릴리스 노트/README에 종료 일정을 명시하고 대체 line으로의 업그레이드 경로를 제공한다.

## Support Expectations

- 일반 운영 이슈: 재현 정보 기준으로 우선순위를 분류해 triage한다.
- 릴리스 인시던트/롤백 절차는 [`playbooks.md`](./playbooks.md)를 따른다.
- 릴리스 승인 전 체크는 [`release-readiness.md`](./release-readiness.md) 증거 항목 기준으로 판단한다.
