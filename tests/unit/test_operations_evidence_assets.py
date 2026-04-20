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


def test_stable_operations_evidence_reference_is_linked_from_entrypoints() -> None:
    reference_doc = REPO_ROOT / "docs" / "operations" / "stable-operations-evidence.md"
    docs_index = (REPO_ROOT / "docs" / "index.md").read_text()
    operations_index = (REPO_ROOT / "docs" / "operations" / "index.md").read_text()
    benchmarks_readme = (REPO_ROOT / "benchmarks" / "README.md").read_text()
    release_readiness = (
        REPO_ROOT / "docs" / "operations" / "release-readiness.md"
    ).read_text()
    playbooks = (REPO_ROOT / "docs" / "operations" / "playbooks.md").read_text()
    readme = (REPO_ROOT / "README.md").read_text()
    readme_ko = (REPO_ROOT / "README.ko.md").read_text()

    assert reference_doc.exists()
    assert "soak-restart-evidence.md" in reference_doc.read_text()
    assert "playbooks.md" in reference_doc.read_text()
    assert "release-readiness.md" in reference_doc.read_text()
    assert (
        "benchmarks/results/mqu122-process-partition-strict-on-20260416T175324Z.json"
        in reference_doc.read_text()
    )
    assert (
        "benchmarks/results/mqu269-process-partition-strict-on-timeout180-20260419T072843Z.json"
        in reference_doc.read_text()
    )
    assert (
        ".omx/artifacts/mqu-269/process-recovery-20260419T073004Z.log"
        in reference_doc.read_text()
    )

    assert "operations/stable-operations-evidence.md" in docs_index
    assert "stable-operations-evidence.md" in operations_index
    assert "stable-operations-evidence.md" in benchmarks_readme
    assert "stable-operations-evidence.md" in release_readiness
    assert "stable-operations-evidence.md" in playbooks
    assert "stable-operations-evidence.md" in readme
    assert "stable-operations-evidence.md" in readme_ko


def test_release_gate_contract_and_pinned_evidence_remain_visible() -> None:
    release_readiness = (
        REPO_ROOT / "docs" / "operations" / "release-readiness.md"
    ).read_text()
    readme = (REPO_ROOT / "README.md").read_text()
    readme_ko = (REPO_ROOT / "README.ko.md").read_text()

    assert "operations/public-contract-v1.md" in readme
    assert "operations/public-contract-v1.md" in readme_ko
    assert "24546725840" in release_readiness
    assert "24546725833" in release_readiness


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
