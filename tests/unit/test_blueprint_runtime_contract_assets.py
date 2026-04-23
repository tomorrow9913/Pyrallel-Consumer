from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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
    requirements = INGRESS_REQUIREMENTS.read_text(encoding="utf-8")
    architecture = INGRESS_ARCHITECTURE.read_text(encoding="utf-8")
    design = INGRESS_DESIGN.read_text(encoding="utf-8")

    assert "PARALLEL_CONSUMER_STRICT_COMPLETION_MONITOR_ENABLED=false" in requirements
    assert "optional wake-up task" in requirements
    assert "strict_completion_monitor_enabled=true" in architecture
    assert "dedicated completion-monitor task" in design


def test_blueprint_runtime_docs_keep_runtime_snapshot_and_secret_boundaries() -> None:
    requirements = INGRESS_REQUIREMENTS.read_text(encoding="utf-8")
    design = INGRESS_DESIGN.read_text(encoding="utf-8")
    observability = OBSERVABILITY_DESIGN.read_text(encoding="utf-8")

    assert "PyrallelConsumer.get_runtime_snapshot()" in requirements
    assert "must not expose TLS/SASL secret values" in requirements
    assert "get_runtime_snapshot()" in design
    assert "must not expose secure Kafka transport fields" in observability
