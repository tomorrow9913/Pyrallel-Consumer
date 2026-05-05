from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _extract_compose_image_values(path: Path) -> list[tuple[str, str]]:
    compose_text = path.read_text(encoding="utf-8")
    in_services = False
    current_service = None
    images: list[tuple[str, str]] = []

    for line in compose_text.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0 and stripped == "services:":
            in_services = True
            current_service = None
            continue

        if not in_services:
            continue

        if indent == 2 and stripped.endswith(":"):
            current_service = stripped[:-1]
            continue

        if current_service is None:
            continue

        if indent == 0 or indent <= 1:
            current_service = None
            in_services = False
            continue
        if indent == 4 and stripped.startswith("image:"):
            _, _, value = stripped.partition(":")
            images.append((current_service, value.strip()))

    return images


def _image_value_is_immutable(image_value: str) -> bool:
    image_value = image_value.strip()
    if "@sha256:" in image_value:
        return image_value.split("@sha256:", 1)[1].strip() != ""

    assert image_value.count(":") >= 1, "missing image tag and digest"
    tag = image_value.rsplit(":", 1)[1]
    return tag != "" and not tag.startswith("latest") and tag != "latest"


def test_e2e_compose_includes_prometheus_and_grafana_services() -> None:
    compose_text = (REPO_ROOT / ".github" / "e2e.compose.yml").read_text()

    assert "kafka-exporter:" in compose_text
    assert "restart: unless-stopped" in compose_text
    assert "prometheus:" in compose_text
    assert "grafana:" in compose_text
    assert "../monitoring/prometheus.yml" in compose_text
    assert "../monitoring/grafana/provisioning" in compose_text
    assert "../monitoring/grafana/dashboards" in compose_text


def test_compose_files_do_not_use_latest_images() -> None:
    compose_files = [
        REPO_ROOT / ".github" / "e2e.compose.yml",
        REPO_ROOT / "docker-compose.yml",
    ]

    for compose_file in compose_files:
        for service_name, image_value in _extract_compose_image_values(compose_file):
            assert _image_value_is_immutable(
                image_value
            ), f"service {service_name} in {compose_file} uses unsupported image ref {image_value!r}"


def test_grafana_prometheus_datasource_uses_stable_uid() -> None:
    datasource_text = (
        REPO_ROOT
        / "monitoring"
        / "grafana"
        / "provisioning"
        / "datasources"
        / "datasource.yml"
    ).read_text()

    assert "uid: prometheus" in datasource_text


def _load_pyrallel_dashboard() -> dict:
    return json.loads(
        (
            REPO_ROOT
            / "monitoring"
            / "grafana"
            / "dashboards"
            / "pyrallel-overview.json"
        ).read_text(encoding="utf-8", errors="strict")
    )


def _dashboard_expressions(dashboard: dict) -> set[str]:
    return {
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        if "expr" in target
    }


def test_grafana_dashboard_is_reference_sample_for_public_metric_surface() -> None:
    dashboard = _load_pyrallel_dashboard()
    dashboard_text = json.dumps(dashboard)
    expressions_text = "\n".join(sorted(_dashboard_expressions(dashboard)))

    assert "Reference / sample" in dashboard["title"]
    required_metric_families = (
        "consumer_processed_total",
        "consumer_commit_failures_total",
        "consumer_dlq_publish_failures_total",
        "consumer_processing_latency_seconds_bucket",
        "consumer_in_flight_count",
        "consumer_parallel_lag",
        "consumer_gap_count",
        "consumer_internal_queue_depth",
        "consumer_oldest_task_duration_seconds",
        "consumer_backpressure_active",
        "consumer_metadata_size_bytes",
        "consumer_resource_signal_status",
        "consumer_resource_cpu_utilization_ratio",
        "consumer_resource_memory_utilization_ratio",
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
        "consumer_process_batch_transport_mode",
        "consumer_process_batch_support_state",
        "consumer_process_batch_timer_flush_supported",
        "consumer_process_batch_demand_flush_supported",
        "consumer_process_batch_recycle_supported",
        "consumer_adaptive_backpressure_configured_max_in_flight",
        "consumer_adaptive_backpressure_effective_max_in_flight",
        "consumer_adaptive_backpressure_min_in_flight",
        "consumer_adaptive_backpressure_scale_up_step",
        "consumer_adaptive_backpressure_scale_down_step",
        "consumer_adaptive_backpressure_cooldown_ms",
        "consumer_adaptive_backpressure_lag_scale_up_threshold",
        "consumer_adaptive_backpressure_low_latency_threshold_ms",
        "consumer_adaptive_backpressure_high_latency_threshold_ms",
        "consumer_adaptive_backpressure_avg_completion_latency_seconds",
        "consumer_adaptive_backpressure_last_decision",
        "consumer_adaptive_concurrency_configured_max_in_flight",
        "consumer_adaptive_concurrency_effective_max_in_flight",
        "consumer_adaptive_concurrency_min_in_flight",
        "consumer_adaptive_concurrency_scale_up_step",
        "consumer_adaptive_concurrency_scale_down_step",
        "consumer_adaptive_concurrency_cooldown_ms",
        "pyrallel_pipeline_stage_messages",
        "pyrallel_pipeline_blocked_messages",
        "pyrallel_pipeline_dispatch_capacity_blocked_messages",
        "pyrallel_pipeline_section_support_state",
        "pyrallel_pipeline_worker_capacity_units",
    )
    for metric_family in required_metric_families:
        assert metric_family in expressions_text

    forbidden_dashboard_terms = (
        "top_k_loads",
        "top_k_depths",
        "worker_id",
        "subqueue_id",
        "exception_text",
        "oldest_age_ms",
        "topic=",
        "partition=",
        "key=",
        "route=",
        "offset=",
    )
    for forbidden_term in forbidden_dashboard_terms:
        assert forbidden_term not in dashboard_text


def test_monitoring_docs_describe_dashboard_as_reference_sample() -> None:
    doc_paths = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "README.ko.md",
        REPO_ROOT / "docs" / "operations" / "guide.en.md",
        REPO_ROOT / "docs" / "operations" / "guide.ko.md",
    ]

    for doc_path in doc_paths:
        doc_text = doc_path.read_text(encoding="utf-8", errors="strict")
        assert "reference/sample" in doc_text
        assert "production opinionated dashboard" in doc_text


def test_monitoring_docs_describe_pipeline_metrics_surface() -> None:
    doc_paths = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "README.ko.md",
        REPO_ROOT / "docs" / "operations" / "guide.en.md",
        REPO_ROOT / "docs" / "operations" / "guide.ko.md",
    ]

    for doc_path in doc_paths:
        doc_text = doc_path.read_text(encoding="utf-8", errors="strict")
        assert "pyrallel_pipeline_stage_messages" in doc_text
        assert "pyrallel_pipeline_blocked_messages" in doc_text
        assert "pyrallel_pipeline_dispatch_capacity_blocked_messages" in doc_text
        assert "pyrallel_pipeline_section_support_state" in doc_text
        assert "pyrallel_pipeline_worker_capacity_units" in doc_text


def test_grafana_dashboard_includes_process_batch_panels() -> None:
    dashboard = _load_pyrallel_dashboard()
    panel_titles = {panel["title"] for panel in dashboard["panels"]}
    expressions = _dashboard_expressions(dashboard)

    assert "Process batch flushes" in panel_titles
    assert "Process batch sizing" in panel_titles
    assert "Process batch timing" in panel_titles
    assert "Process batch support boundary" in panel_titles
    assert "Adaptive control caps" in panel_titles
    assert "Adaptive control parameters" in panel_titles
    for reason in ("size", "timer", "close", "demand"):
        assert f'consumer_process_batch_flush_count{{reason="{reason}"}}' in expressions
    assert "consumer_process_batch_avg_size" in expressions
    assert "consumer_process_batch_avg_main_to_worker_ipc_seconds" in expressions
    assert "consumer_process_batch_avg_worker_exec_seconds" in expressions
    assert "consumer_process_batch_avg_worker_to_main_ipc_seconds" in expressions
    assert "consumer_process_batch_transport_mode" in expressions
    assert "consumer_process_batch_support_state" in expressions
    assert "consumer_process_batch_timer_flush_supported" in expressions
    assert "consumer_process_batch_demand_flush_supported" in expressions
    assert "consumer_process_batch_recycle_supported" in expressions
    assert "consumer_adaptive_backpressure_effective_max_in_flight" in expressions
    assert "consumer_adaptive_concurrency_effective_max_in_flight" in expressions
    assert "consumer_adaptive_backpressure_last_decision" in expressions


def test_grafana_dashboard_includes_pipeline_diagnostics_panels() -> None:
    dashboard = _load_pyrallel_dashboard()
    panel_titles = {panel["title"] for panel in dashboard["panels"]}
    expressions = _dashboard_expressions(dashboard)

    assert "Pipeline stage messages" in panel_titles
    assert "Pipeline blocked reasons" in panel_titles
    assert "Pipeline support and worker capacity" in panel_titles
    assert (
        "sum by (stage, engine_type) (pyrallel_pipeline_stage_messages)" in expressions
    )
    assert (
        "sum by (reason, engine_type) (pyrallel_pipeline_blocked_messages)"
        in expressions
    )
    assert "pyrallel_pipeline_dispatch_capacity_blocked_messages" in expressions
    assert "pyrallel_pipeline_section_support_state" in expressions
    assert "pyrallel_pipeline_worker_capacity_units" in expressions
    forbidden_dashboard_terms = (
        "top_k_loads",
        "top_k_depths",
        "worker_id",
        "subqueue_id",
        "exception_text",
    )
    dashboard_text = json.dumps(dashboard)
    for forbidden_term in forbidden_dashboard_terms:
        assert forbidden_term not in dashboard_text


def test_observability_blueprints_document_pipeline_diagnostics_projection() -> None:
    design_paths = [
        REPO_ROOT
        / "docs"
        / "blueprint"
        / "features"
        / "04-tooling"
        / "01-observability-metrics"
        / "03-design.md",
        REPO_ROOT
        / "docs"
        / "blueprint"
        / "features"
        / "04-tooling"
        / "01-observability-metrics"
        / "03-design.ko.md",
    ]

    for design_path in design_paths:
        design_text = design_path.read_text(encoding="utf-8", errors="strict")
        assert "pyrallel_pipeline_stage_messages" in design_text
        assert "pyrallel_pipeline_blocked_messages" in design_text
        assert "pyrallel_pipeline_dispatch_capacity_blocked_messages" in design_text
        assert "pyrallel_pipeline_section_support_state" in design_text
        assert "pyrallel_pipeline_worker_capacity_units" in design_text
        assert "not_implemented" in design_text
        assert "unavailable" in design_text
        assert "top_k_loads" in design_text
        assert "top_k_depths" in design_text


def test_operations_guides_use_regex_for_process_flush_reason_set() -> None:
    guide_paths = [
        REPO_ROOT / "docs" / "operations" / "guide.en.md",
        REPO_ROOT / "docs" / "operations" / "guide.ko.md",
    ]

    for guide_path in guide_paths:
        guide_text = guide_path.read_text(encoding="utf-8", errors="strict")
        assert (
            'consumer_process_batch_flush_count{reason=~"size|timer|close|demand"}'
            in guide_text
        )
        assert (
            'consumer_process_batch_flush_count{reason="size|timer|close|demand"}'
            not in guide_text
        )


def test_failure_counter_metric_names_are_documented() -> None:
    doc_paths = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "README.ko.md",
        REPO_ROOT / "docs" / "operations" / "guide.en.md",
        REPO_ROOT / "docs" / "operations" / "guide.ko.md",
        REPO_ROOT / "docs" / "operations" / "playbooks.md",
    ]

    for doc_path in doc_paths:
        doc_text = doc_path.read_text(encoding="utf-8", errors="strict")
        assert "consumer_commit_failures_total" in doc_text
        assert 'consumer_commit_failures_total{reason="kafka_exception"}' in doc_text
        assert 'consumer_commit_failures_total{reason="commit_error"}' not in doc_text
        assert "consumer_dlq_publish_failures_total" in doc_text


def test_operations_guides_document_control_plane_commit_clamp_boundary() -> None:
    guide_en = (REPO_ROOT / "docs" / "operations" / "guide.en.md").read_text(
        encoding="utf-8", errors="strict"
    )
    guide_ko = (REPO_ROOT / "docs" / "operations" / "guide.ko.md").read_text(
        encoding="utf-8", errors="strict"
    )

    assert (
        "Commit clamping is computed from the control-plane `WorkManager` "
        "dispatch ledger"
    ) in guide_en
    assert "optional engine capability" not in guide_en

    assert (
        "commit clamp용 최소 in-flight offset은 control-plane `WorkManager` "
        "dispatch ledger에서 계산한다."
    ) in guide_ko
    assert "선택적 엔진 capability" not in guide_ko


def test_e2e_workflow_and_test_cover_prometheus_and_grafana_smoke_checks() -> None:
    workflow_text = (REPO_ROOT / ".github" / "workflows" / "e2e.yml").read_text()
    test_text = (REPO_ROOT / "tests" / "e2e" / "test_monitoring_smoke.py").read_text()

    assert (
        "docker compose -f .github/e2e.compose.yml up -d kafka-1 kafka-exporter prometheus grafana"
        in workflow_text
    )
    assert "uv run pytest tests/e2e -q" in workflow_text
    assert "http://127.0.0.1:9090/-/ready" in test_text
    assert "http://127.0.0.1:3000/api/health" in test_text
    assert "http://127.0.0.1:9091/metrics" in test_text
    assert "http://127.0.0.1:9090/api/v1/targets" in test_text
    assert "http://127.0.0.1:3000/api/datasources/uid/prometheus" in test_text
    assert "http://127.0.0.1:3000/api/search?query=Pyrallel" in test_text
    assert "from confluent_kafka.admin import AdminClient" in test_text
    assert "client.list_topics(timeout=5)" in test_text
    assert '"4000"' in test_text
    assert '"180"' in test_text
