from __future__ import annotations

import json

import pytest

from benchmarks import process_batch_advisor


def _summary_with_process_metrics() -> dict:
    return {
        "options": {"num_messages": 20000, "workloads": ["sleep"]},
        "results": [
            {
                "run_name": "sleep-partition-pyrallel-process",
                "run_type": "process",
                "workload": "sleep",
                "ordering": "partition",
                "throughput_tps": 291.6,
                "process_batch_metrics": {
                    "size_flush_count": 0,
                    "timer_flush_count": 9682,
                    "close_flush_count": 0,
                    "demand_flush_count": 0,
                    "total_flushed_items": 20000,
                    "last_flush_size": 1,
                    "last_flush_wait_seconds": 0.005,
                    "avg_main_to_worker_ipc_seconds": 0.00012,
                    "avg_worker_exec_seconds": 0.0013,
                    "avg_worker_to_main_ipc_seconds": 0.00079,
                },
            }
        ],
    }


def test_process_batch_advisor_recommends_next_run_flags_only() -> None:
    advice = process_batch_advisor.build_process_batch_advice(
        _summary_with_process_metrics()
    )

    assert len(advice) == 1
    item = advice[0]
    assert item.run_name == "sleep-partition-pyrallel-process"
    assert item.runtime_mutation is False
    assert item.next_run_flags == (
        "--process-batch-size",
        "1",
        "--process-max-batch-wait-ms",
        "0",
        "--process-flush-policy",
        "demand_min_residence",
        "--process-demand-flush-min-residence-ms",
        "1",
    )
    assert "process_count" in item.forbidden_knobs
    assert "queue_size" in item.forbidden_knobs
    assert all("process_count" not in flag for flag in item.next_run_flags)
    assert all("queue_size" not in flag for flag in item.next_run_flags)


def test_process_batch_advisor_skips_non_process_or_missing_metrics() -> None:
    summary = {
        "results": [
            {
                "run_name": "sleep-key_hash-pyrallel-async",
                "run_type": "async",
                "ordering": "key_hash",
                "process_batch_metrics": {
                    "timer_flush_count": 10,
                    "total_flushed_items": 10,
                },
            },
            {
                "run_name": "sleep-key_hash-pyrallel-process",
                "run_type": "process",
                "ordering": "key_hash",
                "process_batch_metrics": None,
            },
        ]
    }

    assert process_batch_advisor.build_process_batch_advice(summary) == []


def test_process_batch_advisor_loads_summary_and_formats_markdown(tmp_path) -> None:
    summary_path = tmp_path / "benchmark.json"
    summary_path.write_text(
        json.dumps(_summary_with_process_metrics()),
        encoding="utf-8",
    )

    summary = process_batch_advisor.load_benchmark_summary(summary_path)
    advice = process_batch_advisor.build_process_batch_advice(summary)
    markdown = process_batch_advisor.format_advice_markdown(advice)

    assert "sleep-partition-pyrallel-process" in markdown
    assert "--process-batch-size 1" in markdown
    assert "process_count" in markdown
    assert "queue_size" in markdown


def test_process_batch_advisor_rejects_malformed_summary(tmp_path) -> None:
    summary_path = tmp_path / "bad.json"
    summary_path.write_text(json.dumps(["not", "a", "mapping"]), encoding="utf-8")

    with pytest.raises(ValueError, match="benchmark summary JSON object"):
        process_batch_advisor.load_benchmark_summary(summary_path)
