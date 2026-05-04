"""Regression coverage for the worker-pipe experiment blueprint wording."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = (
    ROOT
    / "docs"
    / "blueprint"
    / "features"
    / "03-execution"
    / "02-process-execution-engine"
)
BENCHMARK_RUNTIME_DOCS_ROOT = (
    ROOT / "docs" / "blueprint" / "features" / "04-tooling" / "02-benchmark-runtime"
)
EXPERIMENT_DOC = DOCS_ROOT / "04-worker-pipe-transport-experiment.md"
INDEX_DOC = DOCS_ROOT / "00-index.md"
INDEX_DOC_KO = DOCS_ROOT / "00-index.ko.md"
REQUIREMENTS_DOC = DOCS_ROOT / "01-requirements.md"
ARCHITECTURE_DOC = DOCS_ROOT / "02-architecture.md"
DESIGN_DOC = DOCS_ROOT / "03-design.md"
BENCHMARK_RUNTIME_DESIGN_KO = BENCHMARK_RUNTIME_DOCS_ROOT / "03-design.ko.md"


def test_process_transport_experiment_doc_keeps_bounded_scope_and_invariants() -> None:
    """The experiment doc must stay bounded, explicit, and implementation-facing."""
    document = EXPERIMENT_DOC.read_text(encoding="utf-8")

    assert "process_transport = worker_pipes" in document
    assert "The live process transport is `worker_pipes`." in document
    assert "--process-route-batch-size 1|8|32|64|128" in document
    assert "Control-plane invariants" in document
    assert "WorkManager` dispatch ledger" in document
    assert "Unsupported matrix for the first slice" in document
    assert "benchmark and release-gate evidence" in document
    assert "not quietly reinterpret" in document


def test_process_transport_experiment_is_listed_from_process_engine_indexes() -> None:
    """The process-engine indexes should surface the experiment blueprint."""
    assert "04-worker-pipe-transport-experiment.md" in INDEX_DOC.read_text(
        encoding="utf-8"
    )
    assert "04-worker-pipe-transport-experiment.ko.md" in INDEX_DOC_KO.read_text(
        encoding="utf-8"
    )


def test_process_engine_docs_capture_long_term_transport_direction() -> None:
    """The process-engine docs should describe the long-term transport direction."""
    index_doc = INDEX_DOC.read_text(encoding="utf-8")
    requirements = REQUIREMENTS_DOC.read_text(encoding="utf-8")
    architecture = ARCHITECTURE_DOC.read_text(encoding="utf-8")
    design = DESIGN_DOC.read_text(encoding="utf-8")

    assert "ordered virtual-queue identity" in index_doc
    assert "`shared_queue` remains historical context only" in requirements
    assert (
        "`worker_pipes` becomes the ordering-preserving parallelism direction"
        in requirements
    )
    assert "Current benchmark and py-spy evidence suggest" in architecture
    assert "ProcessConfig.route_batch_size" in design
    assert "single completion aggregation" in design


def test_korean_process_engine_index_describes_shared_queue_as_historical() -> None:
    index_doc_ko = INDEX_DOC_KO.read_text(encoding="utf-8")

    assert (
        "process execution의 live topology는 `worker_pipes` 단일 경로" in index_doc_ko
    )
    assert (
        "현재 process engine은 submit된 item을 single `multiprocessing.Queue`"
        not in index_doc_ko
    )


def test_korean_benchmark_runtime_design_uses_process_route_batch_flag() -> None:
    design_doc_ko = BENCHMARK_RUNTIME_DESIGN_KO.read_text(encoding="utf-8")

    assert "`--process-route-batch-size`" in design_doc_ko
    assert "`--route-batch-size` |" not in design_doc_ko
