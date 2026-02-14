## Pyrallel-Consumer – Agent Field Guide

This repository implements a high-throughput Kafka consumer modeled after Confluent's Parallel Consumer but tailored for Python's asyncio + multiprocessing runtimes. The control plane (BrokerPoller + WorkManager + OffsetTracker + MetadataEncoder) must remain unaware of which execution engine (async vs process) is active. Keep that invariant top of mind while coding.

### 1. Environment & Toolchain
- **Python**: 3.12 (see `.pre-commit-config.yaml` default_language_version)
- **Dependency manager**: `uv`
  - Install uv: `pip install uv`
  - Sync runtime deps: `uv pip install -r requirements.txt`
  - Sync dev deps: `uv pip install -r dev-requirements.txt`
- **Kafka**: Local Docker Compose cluster expected (see README + e2e tests). The repo includes helper scripts (`producer.py`, `baseline_consumer.py`, `pyrallel_consumer_test.py`).
- **Docs to consult**: `prd_dev.md` (primary dev spec), `prd.md` (design rationale), `GEMINI.md` (handoff + process constraints). If instructions conflict: `prd_dev.md` > `prd.md`. GEMINI rules are mandatory.

### 2. GEMINI.md Non‑Negotiables
1. Update `GEMINI.md` incrementally as you finish meaningful steps (tests added, bug fixed, etc.). No end-of-task batch updates.
2. Production logging must use **percent-style formatting** (e.g., `logger.info("Processed offset %d", offset)`). Never use f-strings in log calls.
3. Never use `assert` in production code—raise explicit exceptions instead. `assert` is allowed only inside tests.
4. Stop immediately if you notice a loop of repeated failing attempts; ask the user for guidance.
5. If sensitive handover info is required, store it outside git-tracked files and reference the path in GEMINI.md.

### 3. Build, Lint, Test Commands
#### 3.1 Core pytest usage
- Full suite: `pytest`
- Unit tests only: `pytest tests/unit/`
- Integration tests: `pytest tests/integration/`
- E2E tests (Kafka required): `pytest tests/e2e/`
- Single file: `pytest tests/unit/control_plane/test_work_manager.py`
- Single test: `pytest tests/unit/control_plane/test_work_manager.py::test_work_manager_initialization`
- Coverage: `pytest --cov=pyrallel_consumer --cov-report=html`
- Async friendliness: repo defaults to `pytest-asyncio` strict mode; prefer `@pytest.mark.asyncio` or `pytest_asyncio.fixture` for async fixtures.

#### 3.2 Pre-commit & linters
- Install hooks: `pre-commit install`
- Run everything: `pre-commit run --all-files`
- Individual tooling (same flags as hooks):
  - Ruff lint: `ruff check .` (auto-fix: `ruff check --fix .`)
  - Ruff format: `ruff format .`
  - isort: `isort --profile black .`
  - autoflake: `autoflake --in-place --remove-all-unused-imports --remove-unused-variable <files>`
  - flake8: `flake8 --ignore=E266,W503,E203,E501,W605 pyrallel_consumer/`
  - mypy: `mypy pyrallel_consumer/`
  - pylint: `pylint --disable=W,C,R,E -j 0 -rn -sn pyrallel_consumer/`
  - bandit: `bandit -q -lll -x 'tests/**/*.py,./contrib/,./.venv/' -r .`

### 4. Repository Layout Highlights
- `pyrallel_consumer/config.py`: Pydantic settings for Kafka + execution configs
- `pyrallel_consumer/control_plane/`: BrokerPoller, OffsetTracker, WorkManager, MetadataEncoder
- `pyrallel_consumer/execution_plane/`: `BaseExecutionEngine`, async + process implementations
- `pyrallel_consumer/logger.py`: LogManager helpers (queue listener setup for multiprocessing)
- `tests/`: unit, integration, and e2e suites layered per PRD
- `baseline_consumer.py`, `producer.py`, `pyrallel_consumer_test.py`: benchmarking scripts for comparison vs baseline consumer and orchestrated async/process tests
- `benchmarks/`: orchestration CLI (`benchmarks/run_parallel_benchmark.py`) + shared stats helpers. Use `uv run python benchmarks/run_parallel_benchmark.py --help` for options.

### 5. Style & Formatting Conventions
#### 5.1 General Python style
- Follow Ruff/flake8 outputs; our ignore list matches Black-style line wrapping (E501) and colon spacing rules.
- Imports ordered by `isort --profile black`. Keep stdlib, third-party, and local groups separated.
- Use type hints everywhere practical; DTOs and execution layers depend on strong typing for mypy.
- Prefer `dataclasses.dataclass(frozen=True)` for DTOs (CompletionEvent, WorkItem, PartitionMetrics, etc.).
- Naming: PascalCase for classes/enums, snake_case for functions/locals, UPPER_SNAKE_CASE for constants/config keys.

#### 5.2 Logging
- Acquire loggers via `logging.getLogger(__name__)` or `LogManager.get_logger(__name__)`.
- Worker processes must call `LogManager.setup_worker_logging(log_queue)`; the main process must keep the QueueListener alive until shutdown finishes.
- All logging messages must use `%`-style formatting per GEMINI (e.g., `logger.warning("Worker %d failed", worker.pid)`).

#### 5.3 Error handling
- Execution engines: wrap worker calls in `try/except Exception` and convert to `CompletionStatus.FAILURE`; log exception tracebacks.
- Control plane: prefer explicit exception types (`RuntimeError`, `ValueError`) with actionable messages.
- Backpressure & rebalance code should prioritize liveness over perfect correctness (see prd.md section 4.2.5). Use timeouts and warn when deadlines pass.

#### 5.4 Concurrency guidelines
- Control plane must remain agnostic to execution modes; all interaction goes through `BaseExecutionEngine`.
- `ExecutionEngine.submit` may block; `WorkManager` must perform capacity checks before awaiting submit.
- Async engine: protect concurrency with semaphores; cancel pending tasks on shutdown.
- Process engine: send sentinel values for shutdown, join with `process_config.worker_join_timeout_ms`, terminate stragglers, and stop the QueueListener last.
- Context propagation: pass epoch + work IDs through WorkItem/CompletionEvent, never rely on shared mutable globals.

#### 5.5 Testing philosophy
- Mandatory TDD workflow (Red → Green → Refactor) per GEMINI + PRD; add tests for each bugfix/feature.
- Contract tests in `tests/unit/execution_plane/test_execution_engine_contract.py` must pass for all engine implementations.
- Use `pytest_asyncio.fixture` for async fixtures (strict mode). For sync fixtures returning async resources, convert to async generator fixtures and `yield` followed by async teardown.

### 6. Design Intent References
- **prd_dev.md**: Developer-focused architecture spec—covers layer responsibilities, DTO contracts, scheduling policy, observability metrics, and configuration schema. Follow its priority ordering when uncertain.
- **prd.md**: Deep rationale—explains why `SortedSet` powers OffsetTracker, why ExecutionEngine submit is blocking, why epoch fencing matters, and how backpressure hysteresis should behave.
- **GEMINI.md**: Project log + process manual. Besides the rules above, it documents progress, outstanding work, and requires incremental updates. If you change architecture/behavior, log it there immediately.

### 7. Observability Expectations
- Metrics DTOs (`SystemMetrics`, `PartitionMetrics`) report true lag, gap counts, blocking offsets/durations, queue sizes, and pause state. Do not invent ad-hoc telemetry—extend DTOs if needed.
- `PrometheusMetricsExporter` translates those DTOs into counters/gauges/histograms. When adding metrics, update `pyrparallel_consumer/metrics_exporter.py`, docs, and `tests/unit/metrics/test_prometheus_exporter.py`.
- `KafkaConfig.metrics` toggles the exporter (`enabled`, `port`). Never start the HTTP server unless the config explicitly enables it.
- Backpressure uses hysteresis thresholds (`pause >= max_in_flight`, `resume <= 0.7 * max_in_flight`). Keep those semantics consistent when modifying WorkManager/BrokerPoller.
- Benchmark scripts measure TPS/latency percentiles; keep throughput metrics comparable between baseline and Pyrallel runs.

### 8. Commit & PR Practices
- Conventional Commit messages (e.g., `feat(control-plane): add rebalance timeout guard`).
- Keep commits small and scoped (one logical unit). The project relies on granular history for agent handoffs.
- Run `pre-commit run --all-files` before pushing; CI is minimal, so local hooks are our safety net.
- Mention relevant PRD sections or GEMINI entries in commit messages when touching core architecture.

### 9. Common Pitfalls & Tips
1. **Log formatting**: accidentally using f-strings will trigger GEMINI violations—grep for `logger.` when reviewing.
2. **Async fixtures**: pytest strict mode will error if sync tests request async fixtures. Use `pytest_asyncio.fixture` for async ones.
3. **Epoch mismatches**: ensure completion events are dropped if their epoch differs from current partition epoch (see `WorkManager` + `BrokerPoller`).
4. **Process shutdown**: always guard against double-shutdown by tracking `_is_shutdown` (already implemented) and ensuring fixtures tear down engines even when tests call `shutdown()` manually.
5. **Backpressure loops**: WorkManager should be the single source of truth for in-flight counts; never read engine internals from BrokerPoller.

### 10. Quick Reference Checklist (before calling work "done")
- [ ] Updated relevant docs (especially `GEMINI.md`, PRD appendices if architecture changed)
- [ ] Added/updated tests (unit + integration/e2e when scope demands)
- [ ] All pytest targets passing locally
- [ ] `pre-commit run --all-files` clean
- [ ] Verified logging sticks to `%` formatting
- [ ] Confirmed no production `assert`
- [ ] Documented new metrics or configuration in README/PRD if applicable

Keep this guide handy whenever you spin up an agent—consistency across contributors (human or AI) keeps this high-throughput consumer reliable.
