# Support / Compatibility Policy

이 문서는 `Pyrallel Consumer`의 지원 범위와 호환성 경계를
prerelease 단계와 `1.0.0` stable 런치 이후로 나눠 정의한다.

## 1. 릴리즈 라인 지원 매트릭스

### 1.1 현재(prerelease-only) 단계

| Release line | 지원 상태 | 메모 |
| --- | --- | --- |
| latest prerelease | Active support | 현재 기본 triage 대상 |
| older prerelease builds | Best effort | 최신 prerelease 재현을 요청할 수 있음 |

### 1.2 stable 런치 이후(`1.0.0`부터 적용)

| Release line | 지원 상태 | 메모 |
| --- | --- | --- |
| latest stable minor | Active support | 기본 지원/회귀 triage 대상 |
| previous stable minor | Security-fix-only | 보안/치명 이슈 중심 |
| prerelease builds newer than latest stable | Best effort | preview 채널, 안정 SLA 없음 |
| older prerelease builds | Best effort | 기본 지원 범위 밖 |

## 2. Python 지원 범위

| Python version | 상태 | 근거 |
| --- | --- | --- |
| 3.12 | Supported | package metadata + CI 대상 |
| 3.13 | Supported | package metadata + CI 대상 |
| < 3.12 | Not supported | 현재 metadata contract 밖 |

## 3. Kafka 호환성 범위

### 3.1 현재 적극 검증 경로

- 저장소의 로컬 Docker / GitHub Actions 기반 Kafka 경로
- 현재 `pyproject.toml`에서 허용하는 `confluent-kafka` 범위
- unit/integration/e2e에서 커버되는 런타임 동작

### 3.2 Best-effort 경로

- 저장소 CI 경로로 검증되지 않은 broker 배포판
- 오래된 client/broker 조합
- 벤더별 Kafka-compatible 환경 중 CI baseline과 동작이 다른 경우

## 4. 이슈 triage 기준

### In-scope

- Python `3.12`/`3.13` 재현
- 저장소가 적극 검증하는 Kafka 경로 재현
- 지원 매트릭스의 active line 회귀

### Best-effort로 낮출 수 있는 항목

- 오래된 prerelease line 이슈
- security-fix-only 범위를 벗어난 이전 stable line 일반 이슈
- 검증되지 않은 broker/stack 이슈

## 5. 운영/보안 문서 연결

- `README.md`, `README.ko.md`
- `SECURITY.md`
- `docs/operations/upgrade-rollback-guide.md`
- `docs/operations/playbooks.md`
- `docs/operations/release-readiness.md`
