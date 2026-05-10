# -*- coding: utf-8 -*-
# File: tests/unit/benchmarks/_benchmark_runtime_support.py
# Role: Provides shared imports, fixtures, and argument builders for benchmark runtime tests.
# Extend here when split benchmark runtime tests need shared fakes, fixtures, or imports.
from __future__ import annotations

import argparse
import asyncio
import socket
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest

from benchmarks import (
    baseline_consumer,
    producer,
    pyrallel_consumer_test,
    run_parallel_benchmark,
)
from benchmarks.stats import BenchmarkResult, BenchmarkStats
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.dto import (
    CompletionStatus,
    ExecutionMode,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine
from pyrallel_consumer.metrics_exporter import PrometheusMetricsExporter

E2E_WORKFLOW = (
    run_parallel_benchmark.Path(__file__).resolve().parents[3]
    / ".github"
    / "workflows"
    / "e2e.yml"
)


__all__ = (
    "Any",
    "BaseExecutionEngine",
    "BenchmarkResult",
    "BenchmarkStats",
    "BrokerPoller",
    "Callable",
    "CompletionStatus",
    "E2E_WORKFLOW",
    "ExecutionMode",
    "PrometheusMetricsExporter",
    "SimpleNamespace",
    "TopicPartition",
    "WorkItem",
    "_build_args",
    "argparse",
    "asyncio",
    "baseline_consumer",
    "benchmark_result",
    "cast",
    "producer",
    "pyrallel_consumer_test",
    "pytest",
    "run_parallel_benchmark",
    "socket",
)


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


def _build_args(**overrides: Any) -> argparse.Namespace:
    parser = run_parallel_benchmark.build_parser()
    args = parser.parse_args([])
    defaults = {
        "skip_baseline": False,
        "skip_async": False,
        "skip_process": True,
        "skip_reset": False,
        "topic_prefix": "demo-topic",
        "baseline_group": "baseline-group",
        "async_group": "async-group",
        "process_group": "process-group",
        "num_partitions": 3,
        "num_messages": 10,
        "num_keys": 2,
        "timeout_sec": 5,
        "bootstrap_servers": "localhost:9092",
        "workloads": ["sleep"],
        "order": ["key_hash"],
        "strict_completion_monitor": ["on"],
        "adaptive_concurrency": ["off"],
        "worker_kind": ["single"],
        "metrics": ["off"],
        "payload_bytes": 0,
        "process_transport": "worker_pipes",
        "route_batch_size": 64,
        "metrics_port": 0,
        "profile": False,
        "json_output": "benchmarks/results/test-runtime.json",
        "log_level": "WARNING",
    }
    for key, value in {**defaults, **overrides}.items():
        setattr(args, key, value)
    return args
