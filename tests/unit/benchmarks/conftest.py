# -*- coding: utf-8 -*-
# File: tests/unit/benchmarks/conftest.py
# Role: Defines package-local fixtures shared by split benchmark unit tests.
# Extend here when benchmark unit suites need package-local pytest fixtures.

from tests.unit.benchmarks._benchmark_runtime_support import BenchmarkResult, pytest


@pytest.fixture
def benchmark_result() -> BenchmarkResult:
    return BenchmarkResult(
        run_name="demo",
        run_type="baseline",
        workload="sleep",
        topic="demo-topic",
        ordering="key_hash",
        process_transport_mode=None,
        messages_processed=10,
        total_time_sec=1.0,
        throughput_tps=10.0,
        avg_processing_ms=1.0,
        p99_processing_ms=1.0,
    )
