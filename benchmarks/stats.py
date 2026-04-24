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
    ordering: str
    messages_processed: int
    total_time_sec: float
    throughput_tps: float
    avg_processing_ms: float
    p99_processing_ms: float
    process_transport_mode: str | None = None
    window_size_messages: int | None = None
    tps_p50_window: float | None = None
    tps_p10_window: float | None = None
    tps_min_window: float | None = None
    final_lag: int | None = None
    final_gap_count: int | None = None
    metrics_observations: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BenchmarkStats:
    def __init__(
        self,
        run_name: str,
        run_type: str,
        workload: str,
        ordering: str,
        topic: str,
        process_transport_mode: str | None = None,
        target_messages: Optional[int] = None,
    ) -> None:
        self.run_name = run_name
        self.run_type = run_type
        self.workload = workload
        self.ordering = ordering
        self.topic = topic
        self.process_transport_mode = process_transport_mode
        self.target_messages = target_messages
        self._start_time: Optional[float] = None
        self._end_time: Optional[float] = None
        self._durations: list[float] = []
        self._completion_times: list[float] = []
        self._processed = 0
        self._window_size_messages = 100
        self._release_gate_observations: list[dict[str, Any]] = []

    def start(self) -> None:
        if self._start_time is None:
            self._start_time = perf_counter()

    def record(self, duration_sec: float, *, completed_at: float | None = None) -> None:
        if self._start_time is None:
            self.start()
        self._durations.append(duration_sec)
        self._processed += 1
        completion_time = completed_at
        if completion_time is None:
            completion_time = perf_counter()
        self._completion_times.append(completion_time)

    def stop(self) -> None:
        if self._start_time is None:
            self.start()
        self._end_time = perf_counter()

    def record_release_gate_observation(
        self,
        *,
        elapsed_sec: float,
        consumer_parallel_lag: int,
        consumer_gap_count: int,
    ) -> None:
        self._release_gate_observations.append(
            {
                "elapsed_sec": elapsed_sec,
                "consumer_parallel_lag": consumer_parallel_lag,
                "consumer_gap_count": consumer_gap_count,
            }
        )

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
        window_tps = self._windowed_tps_samples()
        final_observation = (
            self._release_gate_observations[-1]
            if self._release_gate_observations
            else {}
        )
        return BenchmarkResult(
            run_name=self.run_name,
            run_type=self.run_type,
            workload=self.workload,
            ordering=self.ordering,
            topic=self.topic,
            process_transport_mode=self.process_transport_mode,
            messages_processed=self._processed,
            total_time_sec=total_time,
            throughput_tps=throughput,
            avg_processing_ms=avg_ms,
            p99_processing_ms=p99_ms,
            window_size_messages=self._window_size_messages,
            tps_p50_window=_optional_percentile(window_tps, 50),
            tps_p10_window=_optional_percentile(window_tps, 10),
            tps_min_window=min(window_tps) if window_tps else None,
            final_lag=final_observation.get("consumer_parallel_lag"),
            final_gap_count=final_observation.get("consumer_gap_count"),
            metrics_observations=(
                list(self._release_gate_observations)
                if self._release_gate_observations
                else None
            ),
        )

    def _windowed_tps_samples(self) -> list[float]:
        if len(self._completion_times) < self._window_size_messages:
            return []
        if self._start_time is None:
            return []

        samples: list[float] = []
        window_size = self._window_size_messages
        for end_index in range(
            window_size - 1, len(self._completion_times), window_size
        ):
            start_index = end_index - window_size + 1
            window_start = (
                self._start_time
                if start_index == 0
                else self._completion_times[start_index - 1]
            )
            window_end = self._completion_times[end_index]
            elapsed = max(window_end - window_start, 0.0)
            samples.append((window_size / elapsed) if elapsed > 0 else 0.0)
        return samples


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


def _optional_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    return _percentile(values, percentile)


def write_results_json(
    results: list[BenchmarkResult],
    output_path: Path,
    options: dict[str, Any] | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "options": options or {},
        "performance_improvements": _build_performance_improvements(results),
        "metrics_observations": _merge_metrics_observations(results),
        "results": [result.to_dict() for result in results],
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _merge_metrics_observations(
    results: list[BenchmarkResult],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for result in results:
        if not result.metrics_observations:
            continue
        for observation in result.metrics_observations:
            observations.append(
                {
                    "run_name": result.run_name,
                    "run_type": result.run_type,
                    "workload": result.workload,
                    "ordering": result.ordering,
                    **observation,
                }
            )
    return observations


def _build_performance_improvements(
    results: list[BenchmarkResult],
) -> list[dict[str, Any]]:
    adaptive_off: dict[str, BenchmarkResult] = {}
    adaptive_on: list[BenchmarkResult] = []
    baselines: dict[tuple[str, str], BenchmarkResult] = {}
    pyrallel_results: dict[tuple[str, str], BenchmarkResult] = {}

    for result in results:
        workload_ordering_key = (result.workload, result.ordering)
        if result.run_type == "baseline":
            current_baseline = baselines.get(workload_ordering_key)
            if current_baseline is None or (
                result.throughput_tps > current_baseline.throughput_tps
            ):
                baselines[workload_ordering_key] = result
            continue

        current_pyrallel = pyrallel_results.get(workload_ordering_key)
        if current_pyrallel is None or (
            result.throughput_tps > current_pyrallel.throughput_tps
        ):
            pyrallel_results[workload_ordering_key] = result

        if "-adaptive-off" in result.run_name:
            adaptive_off[_adaptive_comparison_key(result)] = result
        elif "-adaptive-on" in result.run_name:
            adaptive_on.append(result)

    improvements: list[dict[str, Any]] = []
    for candidate in adaptive_on:
        reference = adaptive_off.get(_adaptive_comparison_key(candidate))
        if reference is None:
            continue
        improvements.append(
            _build_improvement_row(
                comparison="adaptive_on_vs_off",
                candidate=candidate,
                reference=reference,
            )
        )

    for workload_ordering_key, candidate in sorted(pyrallel_results.items()):
        reference = baselines.get(workload_ordering_key)
        if reference is None:
            continue
        improvements.append(
            _build_improvement_row(
                comparison="best_pyrallel_vs_baseline",
                candidate=candidate,
                reference=reference,
            )
        )

    return improvements


def _adaptive_comparison_key(result: BenchmarkResult) -> str:
    return result.run_name.replace("-adaptive-on", "-adaptive").replace(
        "-adaptive-off", "-adaptive"
    )


def _build_improvement_row(
    *,
    comparison: str,
    candidate: BenchmarkResult,
    reference: BenchmarkResult,
) -> dict[str, Any]:
    candidate_tps = float(candidate.throughput_tps)
    reference_tps = float(reference.throughput_tps)
    tps_delta = candidate_tps - reference_tps
    if reference_tps > 0:
        tps_delta_pct = (tps_delta / reference_tps) * 100
        improvement_ratio = candidate_tps / reference_tps
    else:
        tps_delta_pct = None
        improvement_ratio = None

    return {
        "comparison": comparison,
        "workload": candidate.workload,
        "ordering": candidate.ordering,
        "run_type": candidate.run_type,
        "candidate_run_name": candidate.run_name,
        "reference_run_name": reference.run_name,
        "candidate_throughput_tps": _round_metric(candidate_tps),
        "reference_throughput_tps": _round_metric(reference_tps),
        "throughput_tps_delta": _round_metric(tps_delta),
        "throughput_tps_delta_pct": _round_optional_metric(tps_delta_pct),
        "improvement_ratio": _round_optional_metric(improvement_ratio),
    }


def _round_metric(value: float) -> float:
    return round(value, 6)


def _round_optional_metric(value: float | None) -> float | None:
    if value is None:
        return None
    return _round_metric(value)
