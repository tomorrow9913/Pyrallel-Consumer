"""Regression coverage for blueprint runtime-contract wording."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INGRESS_REQUIREMENTS = (
    ROOT
    / "docs"
    / "blueprint"
    / "features"
    / "01-ingress"
    / "01-kafka-runtime-ingest"
    / "01-requirements.md"
)
INGRESS_ARCHITECTURE = (
    ROOT
    / "docs"
    / "blueprint"
    / "features"
    / "01-ingress"
    / "01-kafka-runtime-ingest"
    / "02-architecture.md"
)
INGRESS_DESIGN = (
    ROOT
    / "docs"
    / "blueprint"
    / "features"
    / "01-ingress"
    / "01-kafka-runtime-ingest"
    / "03-design.md"
)
OBSERVABILITY_DESIGN = (
    ROOT
    / "docs"
    / "blueprint"
    / "features"
    / "04-tooling"
    / "01-observability-metrics"
    / "03-design.md"
)


def test_ingress_blueprint_documents_strict_completion_monitor_contract() -> None:
    """The ingress blueprint must describe strict-monitor behavior accurately."""
    # Given: inputs for `ingress blueprint documents strict completion...` are prepared.
    requirements = INGRESS_REQUIREMENTS.read_text(encoding="utf-8")
    architecture = INGRESS_ARCHITECTURE.read_text(encoding="utf-8")
    # When: the blueprint runtime contract documentation asset code path is exercised.
    design = INGRESS_DESIGN.read_text(encoding="utf-8")

    # Then: the expected `ingress blueprint documents strict completion...` behavior is asserted.
    assert "PARALLEL_CONSUMER_STRICT_COMPLETION_MONITOR_ENABLED=false" in requirements
    assert "optional wake-up task" in requirements
    assert "strict_completion_monitor_enabled=true" in architecture
    assert "dedicated completion-monitor task" in design


def test_blueprint_runtime_docs_keep_runtime_snapshot_and_secret_boundaries() -> None:
    """Blueprint docs must keep runtime snapshot and secret boundaries explicit."""
    # Given: inputs for `blueprint runtime docs keep runtime snapshot...` are prepared.
    requirements = INGRESS_REQUIREMENTS.read_text(encoding="utf-8")
    design = INGRESS_DESIGN.read_text(encoding="utf-8")
    # When: the blueprint runtime contract documentation asset code path is exercised.
    observability = OBSERVABILITY_DESIGN.read_text(encoding="utf-8")

    # Then: the expected `blueprint runtime docs keep runtime snapshot...` behavior is asserted.
    assert "PyrallelConsumer.get_runtime_snapshot()" in requirements
    assert "must not expose TLS/SASL secret values" in requirements
    assert "get_runtime_snapshot()" in design
    assert "must not expose secure Kafka transport fields" in observability
