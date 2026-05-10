from __future__ import annotations

import json
import math
import resource
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any, Optional


@dataclass
class BenchmarkResult:
    """Represent benchmark result data used by stats."""

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
    worker_kind: str = "single_item_worker"
    constructor: str = "PyrallelConsumer"
    metrics_enabled: bool = False
    large_payload: bool = False
    callback_invocation_count: int | None = None
    callback_item_count: int | None = None
    rss_max_mb: float | None = None
    process_transport_mode: str | None = None
    route_batch_size: int = 1
    process_batch_size: int | None = None
    input_ipc_bytes: int | None = None
    completion_ipc_bytes: int | None = None
    input_ipc_chunks: int | None = None
    completion_ipc_chunks: int | None = None
    items_per_input_ipc: float | None = None
    items_per_completion_ipc: float | None = None
    route_batch_count: int | None = None
    route_batch_item_count: int | None = None
    route_batch_size_avg: float | None = None
    route_batch_size_max: int | None = None
    completion_item_payload_count: int | None = None
    completion_batch_payload_count: int | None = None
    window_size_messages: int | None = None
    tps_p50_window: float | None = None
    tps_p10_window: float | None = None
    tps_min_window: float | None = None
    final_lag: int | None = None
    final_gap_count: int | None = None
    metrics_observations: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Handle to dict within stats."""
        return asdict(self)


class BenchmarkStats:
    """Represent benchmark stats data used by stats."""

    def __init__(
        self,
        run_name: str,
        run_type: str,
        workload: str,
        ordering: str,
        topic: str,
        process_transport_mode: str | None = None,
        route_batch_size: int = 1,
        process_batch_size: int | None = None,
        worker_kind: str = "single_item_worker",
        constructor: str = "PyrallelConsumer",
        metrics_enabled: bool = False,
        large_payload: bool = False,
        target_messages: Optional[int] = None,
    ) -> None:
        self.run_name = run_name
        self.run_type = run_type
        self.workload = workload
        self.ordering = ordering
        self.topic = topic
        self.process_transport_mode = process_transport_mode
        self.route_batch_size = route_batch_size
        self.process_batch_size = process_batch_size
        self.worker_kind = worker_kind
        self.constructor = constructor
        self.metrics_enabled = metrics_enabled
        self.large_payload = large_payload
        self.target_messages = target_messages
        self._start_time: Optional[float] = None
        self._end_time: Optional[float] = None
        self._durations: list[float] = []
        self._completion_times: list[float] = []
        self._processed = 0
        self._window_size_messages = 100
        self._release_gate_observations: list[dict[str, Any]] = []
        self._process_batch_metrics: Any = None
        self._callback_invocation_count = 0
        self._callback_item_count = 0

    def start(self) -> None:
        """Handle start within stats."""
        if self._start_time is None:
            self._start_time = perf_counter()

    def record(self, duration_sec: float, *, completed_at: float | None = None) -> None:
        """Handle record within stats."""
        if self._start_time is None:
            self.start()
        self._durations.append(duration_sec)
        self._processed += 1
        completion_time = completed_at
        if completion_time is None:
            completion_time = perf_counter()
        self._completion_times.append(completion_time)

    def stop(self) -> None:
        """Handle stop within stats."""
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
        """Record release gate observation for stats."""
        self._release_gate_observations.append(
            {
                "elapsed_sec": elapsed_sec,
                "consumer_parallel_lag": consumer_parallel_lag,
                "consumer_gap_count": consumer_gap_count,
            }
        )

    def record_process_batch_metrics(self, metrics: Any) -> None:
        """Record latest process runtime metrics for benchmark summary."""
        self._process_batch_metrics = metrics

    def record_callback_invocation(self, *, item_count: int = 1) -> None:
        """Record one user-worker callback invocation for benchmark evidence."""
        self._callback_invocation_count += 1
        self._callback_item_count += item_count

    @property
    def processed(self) -> int:
        """Handle processed within stats."""
        return self._processed

    def completed_target(self) -> bool:
        """Handle completed target within stats."""
        return (
            self.target_messages is not None and self._processed >= self.target_messages
        )

    def summary(self) -> BenchmarkResult:
        """Handle summary within stats."""
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
            worker_kind=self.worker_kind,
            constructor=self.constructor,
            metrics_enabled=self.metrics_enabled,
            large_payload=self.large_payload,
            callback_invocation_count=self._callback_invocation_count,
            callback_item_count=self._callback_item_count,
            rss_max_mb=_rss_max_mb(),
            process_transport_mode=self.process_transport_mode,
            route_batch_size=self.route_batch_size,
            process_batch_size=self.process_batch_size,
            input_ipc_bytes=_metric_value(
                self._process_batch_metrics,
                "input_ipc_bytes",
            ),
            completion_ipc_bytes=_metric_value(
                self._process_batch_metrics,
                "completion_ipc_bytes",
            ),
            input_ipc_chunks=_metric_value(
                self._process_batch_metrics,
                "input_ipc_chunks",
            ),
            completion_ipc_chunks=_metric_value(
                self._process_batch_metrics,
                "completion_ipc_chunks",
            ),
            items_per_input_ipc=_metric_value(
                self._process_batch_metrics,
                "items_per_input_ipc",
            ),
            items_per_completion_ipc=_metric_value(
                self._process_batch_metrics,
                "items_per_completion_ipc",
            ),
            route_batch_count=_metric_value(
                self._process_batch_metrics,
                "route_batch_count",
            ),
            route_batch_item_count=_metric_value(
                self._process_batch_metrics,
                "route_batch_item_count",
            ),
            route_batch_size_avg=_metric_value(
                self._process_batch_metrics,
                "route_batch_size_avg",
            ),
            route_batch_size_max=_metric_value(
                self._process_batch_metrics,
                "route_batch_size_max",
            ),
            completion_item_payload_count=_metric_value(
                self._process_batch_metrics,
                "completion_item_payload_count",
            ),
            completion_batch_payload_count=_metric_value(
                self._process_batch_metrics,
                "completion_batch_payload_count",
            ),
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
        """Handle windowed tps samples within stats."""
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
    """Handle safe mean within stats."""
    if not values:
        return 0.0
    return mean(values)


def _percentile(values: list[float], percentile: float) -> float:
    """Handle percentile within stats."""
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
    """Handle optional percentile within stats."""
    if not values:
        return None
    return _percentile(values, percentile)


def _metric_value(metrics: Any, field_name: str) -> Any:
    """Return a runtime metric value or None when unavailable."""
    if metrics is None:
        return None
    return getattr(metrics, field_name, None)


def _rss_max_mb() -> float:
    """Return maximum resident set size in MiB for artifact provenance."""
    rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if rss <= 0:
        return 0.0
    # Linux reports KiB; macOS reports bytes.
    if rss > 10_000_000:
        return rss / (1024 * 1024)
    return rss / 1024


def write_results_json(
    results: list[BenchmarkResult],
    output_path: Path,
    options: dict[str, Any] | None = None,
    artifact_metadata: dict[str, Any] | None = None,
) -> None:
    """Handle write results json within stats."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "options": options or {},
        "artifact_metadata": artifact_metadata or {},
        "performance_improvements": _build_performance_improvements(results),
        "metrics_observations": _merge_metrics_observations(results),
        "results": [result.to_dict() for result in results],
    }
    output_path.write_text(
        json.dumps(payload, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def _merge_metrics_observations(
    results: list[BenchmarkResult],
) -> list[dict[str, Any]]:
    """Handle merge metrics observations within stats."""
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
    """Build performance improvements for stats."""
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
    """Handle adaptive comparison key within stats."""
    return result.run_name.replace("-adaptive-on", "-adaptive").replace(
        "-adaptive-off", "-adaptive"
    )


def _build_improvement_row(
    *,
    comparison: str,
    candidate: BenchmarkResult,
    reference: BenchmarkResult,
) -> dict[str, Any]:
    """Build improvement row for stats."""
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
    """Handle round metric within stats."""
    return round(value, 6)


def _round_optional_metric(value: float | None) -> float | None:
    """Handle round optional metric within stats."""
    if value is None:
        return None
    return _round_metric(value)
