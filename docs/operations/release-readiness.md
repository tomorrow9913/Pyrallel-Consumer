# Stable Release Readiness Checklist

이 문서는 `Pyrallel Consumer`를 alpha/prerelease에서 상업적 이용 가능한 stable release로 승격하기 전에 확인해야 하는 항목을 우선순위별로 정리한 체크리스트다.

## How To Use

- `P0`: stable 선언 전에 반드시 닫아야 하는 항목
- `P1`: stable 직전까지 가능한 한 닫아야 하는 항목
- `P2`: stable 이후 운영 성숙도를 올리기 위한 항목

각 항목은 다음 세 가지를 함께 본다.

- **What**: 무엇을 확인하거나 구현해야 하는가
- **Evidence**: 무엇을 근거로 완료를 판단할 것인가
- **Owner hint**: 주로 어느 문서/코드 영역에서 다뤄야 하는가

## P0: Stable 승격 전 필수

- [ ] **알파 메타데이터 제거**
  - What: `version`, classifier, README release policy가 stable 상태와 일치해야 한다.
  - Evidence: `pyproject.toml`에서 alpha classifier 제거, stable 버전 반영, README 정책 문구 수정.
  - Owner hint: `pyproject.toml`, `README.md`, `README.ko.md`

- [ ] **핵심 public contract 동결**
  - What: ordering mode 기본 가이드, DLQ payload default, commit public surface, rebalance state strategy 기본값을 stable contract로 명시한다.
  - Evidence: 열린 결정 문서가 stable 정책으로 닫히고 운영/README에 반영된다.
  - Owner hint: `docs/blueprint/04-open-decisions.md`, `README*`, `docs/operations/*`

- [x] **실브로커 E2E에 process mode 포함**
  - What: 실제 Kafka를 띄운 상태에서 async/process 엔진 모두에 대해 ordering, retry, DLQ, rebalance/restart 핵심 경로를 검증한다.
  - Evidence: `tests/e2e/test_ordering.py`와 `tests/e2e/test_process_recovery.py`에서 process mode 실브로커 E2E가 통과한다.
  - Owner hint: `tests/e2e/`, `.github/workflows/e2e.yml`

- [x] **P0/E2E Gate (broker-backed release gate)**
  - What: 릴리스 경로에서 broker-backed E2E가 skip 없이 실행되고, run/artifact 증거가 문서에 고정 집계되어야 한다.
  - Evidence: `e2e` run/artifact + `release-verify` run/artifact를 함께 기록하고, `Run broker-backed E2E tests (release gate)` step success를 확인한다.
  - Fresh evidence (2026-04-17):
    - `e2e` run: https://github.com/tomorrow9913/Pyrallel-Consumer/actions/runs/24546725840
    - `e2e` artifact: https://github.com/tomorrow9913/Pyrallel-Consumer/actions/runs/24546725840/artifacts/6488389048
    - `release-verify` run: https://github.com/tomorrow9913/Pyrallel-Consumer/actions/runs/24546725833
    - `release-verify` artifact: https://github.com/tomorrow9913/Pyrallel-Consumer/actions/runs/24546725833/artifacts/6488394673
  - Owner hint: `.github/workflows/e2e.yml`, `.github/workflows/release-verify.yml`, `docs/operations/release-readiness.md`

- [ ] **CI quality gate 강화**
  - What: 최소한 lint/type/security/build/artifact check가 PR과 push에서 자동으로 검증돼야 한다.
  - Evidence: GitHub Actions가 `ruff`, `mypy`, `bandit`, `uv build`, `twine check`를 수행한다.
  - Owner hint: `.github/workflows/ci.yml`, `pyproject.toml`

- [ ] **배포 산출물 검증 표준화**
  - What: 어떤 artifact를 검증 대상으로 삼는지 명확히 하고 stale artifact가 릴리스 판단에 섞이지 않게 한다.
  - Evidence: release build 절차가 문서화되고 `twine check` 대상이 fresh artifact로 고정된다.
  - Owner hint: `CHANGELOG.md`, release workflow/commands, `GEMINI.md`

- [x] **보안 연락 경로와 책임 명시**
  - What: 공개 이슈가 아닌 보안 제보 채널과 응답 기대치를 문서화한다.
  - Evidence: `SECURITY.md`에 비공개 제보 경로와 응답 기준이 정의되고, `README*`/docs index에서 `SECURITY.md`로 직접 연결된다.
  - Owner hint: `SECURITY.md`, `README*`, `docs/index.md`

## P1: Stable 직전 권장

- [ ] **장시간 soak / 재시작 회복 검증**
  - What: 장시간 처리 중 backpressure, rebalance, worker recycle, restart 후 offset/DLQ 동작을 검증한다.
  - Evidence: soak 시나리오 문서와 결과 기록, 반복 가능한 명령 또는 workflow.
  - Owner hint: `benchmarks/`, `tests/e2e/`, `docs/operations/playbooks.md`

- [x] **지원 범위와 호환성 정책 문서화**
  - What: 지원 Python 버전, Kafka 브로커/클라이언트 호환 범위, deprecation policy를 정의한다.
  - Evidence: `docs/operations/support-policy.md`에 release-line 지원 매트릭스와 Python/Kafka 경계가 문서화되고 `README*`에서 직접 참조한다.
  - Owner hint: `README*`, `SECURITY.md`, `docs/operations/support-policy.md`

- [x] **업그레이드/롤백 가이드 추가**
  - What: alpha 사용자 또는 이전 버전 사용자가 stable로 올릴 때 확인할 설정/동작 차이를 안내한다.
  - Evidence: `docs/operations/upgrade-rollback-guide.md`가 추가되고 `README*`/operations index에서 링크되며, `playbooks.md`에 release incident rollback runbook이 정의된다.
  - Owner hint: `docs/operations/*`, `CHANGELOG.md`, `README*`

- [ ] **성능 회귀 기준선 고정**
  - What: workload별 허용 TPS/p99 범위를 advisory가 아니라 release review 입력값으로 고정한다.
  - Evidence: benchmark baseline 문서와 비교 절차 존재.
  - Owner hint: `docs/operations/playbooks.md`, `benchmarks/README.md`

## P2: Stable 이후 성숙도 향상

- [ ] **릴리스 자동화**
  - What: tag 기반 build, artifact 검증, publish, release note 생성을 자동화한다.
  - Evidence: manual publish 없이 repeatable release workflow가 동작한다.
  - Owner hint: `.github/workflows/`

- [ ] **지원/운영 SLO 정리**
  - What: response targets, security patch cadence, support expectations를 정리한다.
  - Evidence: 운영 문서 또는 support policy 문서.
  - Owner hint: `docs/operations/*`

- [ ] **추가 observability assets**
  - What: canonical dashboard, alert rule examples, runbook drill 결과를 제공한다.
  - Evidence: monitoring assets와 운영 문서가 함께 유지된다.
  - Owner hint: `monitoring/`, `docs/operations/*`

### Current `type: ignore` inventory (reviewed)

현재 runtime/source 기준 `type: ignore`는 주로 **서드파티 stub 한계** 또는 **의도적인 private-attribute 경계**를 설명하는 용도다.

- `pyrallel_consumer/control_plane/offset_tracker.py`
  - `cachetools`, `sortedcontainers`의 untyped import 보정
- `pyrallel_consumer/control_plane/work_queue_topology.py`
  - `asyncio.Queue` 내부/private attribute 접근 보정
- `pyrallel_consumer/control_plane/broker_rebalance_support.py`
  - `confluent_kafka` stub이 `KafkaTopicPartition(..., metadata=...)`를 모델링하지 않는 경계 보정
- `pyrallel_consumer/control_plane/broker_poller.py`
  - Kafka headers typing mismatch 보정
- `pyrallel_consumer/execution_plane/process_engine.py`
  - `msgpack` untyped import, multiprocessing queue arg typing 보정

테스트 코드에도 white-box/private-attribute 검증을 위한 추가 `type: ignore`가 남아 있지만, stable readiness 관점에서는 우선 runtime/source ignore를 inventory화하고 정당화하는 것을 기준선으로 삼는다. 이후 stable 전에는 이 목록을 줄이거나, 남길 경우 같은 수준의 근거를 유지해야 한다.

## Recommended Verification Commands

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/unit -q --ignore=tests/unit/benchmarks
UV_CACHE_DIR=.uv-cache uv run pytest tests/integration -q
UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/benchmarks -q
UV_CACHE_DIR=.uv-cache uv run pytest tests/e2e -q
UV_CACHE_DIR=.uv-cache uv run ruff check .
UV_CACHE_DIR=.uv-cache uv run mypy pyrallel_consumer
UV_CACHE_DIR=.uv-cache uv run bandit -q -lll -r pyrallel_consumer
UV_CACHE_DIR=.uv-cache uv build
UV_CACHE_DIR=.uv-cache uv run twine check dist/pyrallel_consumer-*
```

## Current Assessment Snapshot

- 현재 상태는 **hardening된 alpha**로 보는 것이 맞다.
- `key_hash`/`partition` ordering에 더해 process mode의 retry, DLQ, in-flight rebalance, restart/offset continuity에 대한 실브로커 E2E도 확보됐다.
- 당장 stable 승격을 막는 핵심은 이제 `알파 메타데이터`, `남은 public contract 결정`, 그리고 P1 성격의 장시간/운영 성숙도 검증 쪽에 더 가깝다.
- 이번 라운드에서는 process recovery 경로의 실브로커 증거를 확보했고, 이후 라운드는 문서 정책 정리와 release gate 정밀화에 집중하면 된다.

## Type Ignore Inventory Snapshot

2026-04-09 기준으로 production 코드의 `type: ignore`는 주로 아래 세 부류로 정리된다.

- **third-party stub gap**
  - `pyrallel_consumer/control_plane/offset_tracker.py`
  - `pyrallel_consumer/execution_plane/process_engine.py`
  - 이유: `cachetools`, `sortedcontainers`, `msgpack`의 typing 정보가 런타임 사용 범위와 완전히 맞지 않음
- **private/internal attribute boundary**
  - `pyrallel_consumer/control_plane/work_queue_topology.py`
  - 이유: `asyncio.Queue` 내부 상태를 읽거나 조정하는 경계가 typing stub에 노출되지 않음
- **confluent-kafka / multiprocessing call-site stub gap**
  - `pyrallel_consumer/control_plane/broker_rebalance_support.py`
  - `pyrallel_consumer/control_plane/broker_poller.py`
  - `pyrallel_consumer/execution_plane/process_engine.py`
  - 이유: `KafkaTopicPartition(metadata=...)`, producer headers, multiprocessing queue payload 타입이 런타임에서는 유효하지만 현재 stub이 좁게 선언됨

즉, 현재 inventory는 **무분별한 ignore 누적이라기보다 stub 한계 또는 의도적인 내부 경계 접근을 문서화한 상태**에 가깝다. stable 전환 전에는 이 목록을 다시 점검하되, 지금 단계에서는 각 ignore가 왜 필요한지 설명 가능한 상태를 유지하는 것을 우선한다.
