# Upgrade / Rollback Guide

이 문서는 prerelease/stable 전환 구간에서 운영자가 따라야 할
업그레이드/롤백 절차를 정리한다.

## 1. 원칙

- 지원 범위는 `support-policy.md`를 따른다.
- 장애 시에는 원인 완전 분석보다 서비스 안정성 확보를 위한 빠른 롤백을 우선한다.
- 릴리즈 인시던트 대응은 `playbooks.md`의
  `Release Incident / Rollback Runbook`을 함께 따른다.

## 2. 업그레이드 전 체크리스트

1. 현재 운영 버전과 설정 스냅샷을 기록한다.
2. `CHANGELOG.md` 변경사항(특히 ordering/retry/DLQ/commit)을 확인한다.
3. 이전 정상 버전 artifact 또는 lockfile을 롤백용으로 확보한다.
4. `support-policy.md`의 릴리즈 라인 지원 상태를 확인한다.

## 3. 권장 업그레이드 절차

1. 스테이징에 신규 버전을 pin 설치한다.
2. smoke 검증(기동/종료, consume/commit, DLQ 경로)을 수행한다.
3. 짧은 soak로 lag/gap/backpressure/oldest-task 지표를 확인한다.
4. 이상 없으면 점진 롤아웃한다.

## 4. 롤백 절차(운영자)

1. 롤아웃을 중지한다.
2. 마지막 정상 버전으로 pin/lockfile을 되돌린다.
3. 컨슈머 재기동 후 핵심 지표 회복을 확인한다.
4. 롤백 시점, 영향 범위, 임시 완화책을 기록한다.

## 5. 롤백 절차(유지보수자)

문제 릴리즈가 배포된 경우 `release-versioning-policy`를 따른다.

- 기존 버전 overwrite 금지
- 기존 tag 재사용 금지
- `yank + forward fix` 원칙 적용
