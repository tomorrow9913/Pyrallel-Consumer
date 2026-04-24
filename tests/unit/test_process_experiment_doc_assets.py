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
EXPERIMENT_DOC = DOCS_ROOT / "04-worker-pipe-transport-experiment.md"
INDEX_DOC = DOCS_ROOT / "00-index.md"
INDEX_DOC_KO = DOCS_ROOT / "00-index.ko.md"
REQUIREMENTS_DOC = DOCS_ROOT / "01-requirements.md"
ARCHITECTURE_DOC = DOCS_ROOT / "02-architecture.md"
DESIGN_DOC = DOCS_ROOT / "03-design.md"


def test_process_transport_experiment_doc_keeps_bounded_scope_and_invariants() -> None:
    """The experiment doc must stay bounded, explicit, and implementation-facing."""
    document = EXPERIMENT_DOC.read_text(encoding="utf-8")

    assert 'transport_mode: Literal["shared_queue", "worker_pipes"]' in document
    assert "The default must remain `shared_queue`." in document
    assert "--process-transport shared_queue|worker_pipes" in document
    assert "Control-plane invariants" in document
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
    assert "`shared_queue` remains the compatibility/default path" in requirements
    assert (
        "`worker_pipes` becomes the ordering-preserving parallelism direction"
        in requirements
    )
    assert "Current benchmark and py-spy evidence suggest" in architecture
    assert "ProcessConfig.transport_mode" in design
    assert "single completion aggregation" in design
