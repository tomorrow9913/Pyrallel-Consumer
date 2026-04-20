from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PROCESS_MODE_METRICS = [
    "consumer_process_batch_flush_count",
    "consumer_process_batch_avg_size",
    "consumer_process_batch_last_size",
    "consumer_process_batch_last_wait_seconds",
    "consumer_process_batch_buffered_items",
    "consumer_process_batch_buffered_age_seconds",
    "consumer_process_batch_last_main_to_worker_ipc_seconds",
    "consumer_process_batch_avg_main_to_worker_ipc_seconds",
    "consumer_process_batch_last_worker_exec_seconds",
    "consumer_process_batch_avg_worker_exec_seconds",
    "consumer_process_batch_last_worker_to_main_ipc_seconds",
    "consumer_process_batch_avg_worker_to_main_ipc_seconds",
]


def test_process_mode_metrics_operator_guidance_lists_exposed_metrics() -> None:
    docs = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "README.ko.md",
        REPO_ROOT / "docs" / "operations" / "guide.en.md",
        REPO_ROOT / "docs" / "operations" / "guide.ko.md",
    ]

    for path in docs:
        text = path.read_text()
        for metric in PROCESS_MODE_METRICS:
            assert (
                metric in text
            ), f"{metric} missing from {path.relative_to(REPO_ROOT)}"

    for path in docs[2:]:
        text = path.read_text()
        assert (
            'consumer_process_batch_flush_count{reason=~"size|timer|close|demand"}'
            in text
        )
        assert (
            'consumer_process_batch_flush_count{reason="size|timer|close|demand"}'
            not in text
        )
        assert "`worker_to_main_ipc_seconds`" not in text
        assert "`total_in_flight` suggests" not in text
