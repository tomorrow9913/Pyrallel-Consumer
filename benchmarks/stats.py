from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any, Optional


@dataclass
class BenchmarkResult:
    run_name: str
    run_type: str
    workload: str
    topic: str
    messages_processed: int
    total_time_sec: float
    throughput_tps: float
    avg_processing_ms: float
    p99_processing_ms: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BenchmarkStats:
    def __init__(
        self,
        run_name: str,
        run_type: str,
        workload: str,
        topic: str,
        target_messages: Optional[int] = None,
    ) -> None:
        self.run_name = run_name
        self.run_type = run_type
        self.workload = workload
        self.topic = topic
        self.target_messages = target_messages
        self._start_time: Optional[float] = None
        self._end_time: Optional[float] = None
        self._durations: list[float] = []
        self._processed = 0

    def start(self) -> None:
        if self._start_time is None:
            self._start_time = perf_counter()

    def record(self, duration_sec: float) -> None:
        if self._start_time is None:
            self.start()
        self._durations.append(duration_sec)
        self._processed += 1

    def stop(self) -> None:
        if self._start_time is None:
            self.start()
        self._end_time = perf_counter()

    @property
    def processed(self) -> int:
        return self._processed

    def completed_target(self) -> bool:
        return (
            self.target_messages is not None and self._processed >= self.target_messages
        )

    def summary(self) -> BenchmarkResult:
        if self._start_time is None:
            raise RuntimeError("BenchmarkStats.summary() called before start()")
        end_time = self._end_time or perf_counter()
        total_time = max(end_time - self._start_time, 0.0)
        throughput = (self._processed / total_time) if total_time > 0 else 0.0
        avg_ms = _safe_mean(self._durations) * 1000
        p99_ms = _percentile(self._durations, 99) * 1000
        return BenchmarkResult(
            run_name=self.run_name,
            run_type=self.run_type,
            workload=self.workload,
            topic=self.topic,
            messages_processed=self._processed,
            total_time_sec=total_time,
            throughput_tps=throughput,
            avg_processing_ms=avg_ms,
            p99_processing_ms=p99_ms,
        )


def _safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return mean(values)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_values = sorted(values)
    rank = (percentile / 100.0) * (len(sorted_values) - 1)
    lower_index = math.floor(rank)
    upper_index = math.ceil(rank)
    if lower_index == upper_index:
        return sorted_values[int(rank)]
    lower_value = sorted_values[lower_index]
    upper_value = sorted_values[upper_index]
    fraction = rank - lower_index
    return lower_value + (upper_value - lower_value) * fraction


def write_results_json(
    results: list[BenchmarkResult],
    output_path: Path,
    options: dict[str, Any] | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "options": options or {},
        "results": [result.to_dict() for result in results],
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
