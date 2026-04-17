# Docs Index

`docs/`는 세 가지 문서군으로 나뉜다.

## 1. Blueprint

- **[blueprint/00-index.md](./blueprint/00-index.md)** - 서비스 정의, 요구사항, 아키텍처, feature blueprint의 메인 진입점

이 문서군에는 다음이 포함된다.

- overview / requirements / architecture / open decisions / context restoration
- feature별 `00-index -> 01-requirements -> 02-architecture -> 03-design`

## 2. Operations

- **[../SECURITY.md](../SECURITY.md)** - 비공개 취약점 제보 경로와 보안 응답 정책
- **[operations/index.md](./operations/index.md)** - 운영 문서 entrypoint
- **[operations/guide.ko.md](./operations/guide.ko.md)** - 한국어 운영 가이드
- **[operations/guide.en.md](./operations/guide.en.md)** - 영문 운영 가이드
- **[operations/playbooks.md](./operations/playbooks.md)** - 운영 플레이북과 튜닝 절차
- **[operations/release-readiness.md](./operations/release-readiness.md)** - stable release readiness 체크리스트
- **[operations/support-policy.md](./operations/support-policy.md)** - 지원 범위와 호환성 정책
- **[operations/upgrade-rollback-guide.md](./operations/upgrade-rollback-guide.md)** - 업그레이드/롤백 가이드

## 3. Plans

- **[plans/](./plans/)** - 날짜별 design / implementation plan 아카이브

## 빠른 안내

- 현재 runtime 구조와 feature 청사진을 보려면 `blueprint/`
- 운영과 장애 대응만 보려면 `operations/`
- 과거 작업 계획과 설계 결정을 보려면 `plans/`
