from __future__ import annotations

import ast
import json
import re
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


def _exporter_public_metric_families() -> set[str]:
    exporter_tree = ast.parse(
        (REPO_ROOT / "pyrallel_consumer" / "metrics_exporter.py").read_text(
            encoding="utf-8", errors="strict"
        )
    )
    metric_families: set[str] = set()
    for node in ast.walk(exporter_tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {"Counter", "Gauge", "Histogram"}:
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if not isinstance(node.args[0].value, str):
            continue
        family = node.args[0].value
        if node.func.id == "Histogram":
            family = f"{family}_bucket"
        metric_families.add(family)
    return metric_families


def _dashboard_metric_family_references(dashboard: dict) -> set[str]:
    exporter_families = _exporter_public_metric_families()
    metric_pattern = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
    references: set[str] = set()
    for expression in _dashboard_expressions(dashboard):
        references.update(set(metric_pattern.findall(expression)) & exporter_families)
    return references


def _dashboard_panels_by_title(dashboard: dict) -> dict[str, dict]:
    return {panel["title"]: panel for panel in dashboard["panels"]}


def _panel_expressions(panel: dict) -> set[str]:
    return {target["expr"] for target in panel.get("targets", []) if "expr" in target}


def _panel_targets_by_expr(panel: dict) -> dict[str, dict]:
    return {
        target["expr"]: target
        for target in panel.get("targets", [])
        if "expr" in target
    }


def _walk_json_values(value: object) -> list[object]:
    values = [value]
    if isinstance(value, dict):
        for child_value in value.values():
            values.extend(_walk_json_values(child_value))
    elif isinstance(value, list):
        for child_value in value:
            values.extend(_walk_json_values(child_value))
    return values


def _section_titles_before_panel(dashboard: dict, panel_title: str) -> list[str]:
    panels = sorted(
        dashboard["panels"],
        key=lambda panel: (
            panel.get("gridPos", {}).get("y", 0),
            panel.get("gridPos", {}).get("x", 0),
        ),
    )
    sections: list[str] = []
    for panel in panels:
        if panel.get("type") == "row":
            sections.append(panel["title"])
            continue
        if panel["title"] == panel_title:
            return sections
    raise AssertionError(f"panel not found: {panel_title}")


def _section_for_panel(dashboard: dict, panel_title: str) -> str:
    sections = _section_titles_before_panel(dashboard, panel_title)
    if not sections:
        return ""
    return sections[-1]


def _panel_sort_key(panel: dict) -> tuple[int, int]:
    grid = panel.get("gridPos", {})
    return grid.get("y", 0), grid.get("x", 0)


def _section_row_y(dashboard: dict, section_title: str) -> int:
    for panel in dashboard["panels"]:
        if panel.get("type") == "row" and panel.get("title") == section_title:
            return panel["gridPos"]["y"]
    raise AssertionError(f"section not found: {section_title}")


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
        "consumer_process_route_batch_count",
        "consumer_process_route_batch_items",
        "consumer_process_route_batch_avg_size",
        "consumer_process_route_batch_max_size",
        "consumer_process_ipc_items_per_input_payload",
        "consumer_process_ipc_items_per_completion_payload",
        "consumer_process_completion_item_payload_count",
        "consumer_process_completion_batch_payload_count",
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
        "pyrallel_pipeline_poll_records_total",
        "pyrallel_pipeline_poll_events_total",
        "pyrallel_pipeline_subqueue_items",
        "pyrallel_pipeline_subqueues",
        "pyrallel_pipeline_settlement_blocker_state",
        "pyrallel_pipeline_completion_to_commit_latency_seconds_bucket",
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


def test_reference_dashboard_catalog_covers_exporter_public_metric_surface() -> None:
    dashboard = _load_pyrallel_dashboard()
    exporter_families = _exporter_public_metric_families()
    dashboard_references = _dashboard_metric_family_references(dashboard)

    assert len(exporter_families) == 67
    assert exporter_families - dashboard_references == set()


def test_reference_catalog_does_not_use_dropdown_or_selector_hiding() -> None:
    dashboard = _load_pyrallel_dashboard()
    dashboard_values = _walk_json_values(dashboard)

    assert dashboard.get("templating", {}).get("list", []) == []
    for panel in dashboard["panels"]:
        assert panel.get("transformations", []) == []
        assert "hideFrom" not in json.dumps(panel)

        reduce_options = panel.get("options", {}).get("reduceOptions")
        if reduce_options is not None:
            assert reduce_options.get("fields", "") == ""
            assert reduce_options.get("values") is False

    forbidden_selector_terms = (
        "queryParam",
        "multiFormat",
        "currentMetric",
        "selectedMetric",
        "metricSelector",
    )
    dashboard_text_values = [
        value for value in dashboard_values if isinstance(value, str)
    ]
    for forbidden_term in forbidden_selector_terms:
        assert forbidden_term not in dashboard_text_values


def test_reference_dashboard_does_not_use_table_panels() -> None:
    dashboard = _load_pyrallel_dashboard()

    table_panel_titles = [
        panel["title"] for panel in dashboard["panels"] if panel.get("type") == "table"
    ]
    assert table_panel_titles == []

    for panel in dashboard["panels"]:
        options = panel.get("options", {})
        assert "showHeader" not in options
        assert "cellHeight" not in options
        assert "enablePagination" not in options


def test_catalog_reference_panels_expose_metric_families_without_selector_targets() -> (
    None
):
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)
    expected_expressions_by_panel = {
        "Pipeline support matrix": {"pyrallel_pipeline_section_support_state"},
        "Pipeline stage messages": {
            "sum by (stage, engine_type) (pyrallel_pipeline_stage_messages)"
        },
        "Pipeline blocked reasons": {
            "sum by (reason, engine_type) (pyrallel_pipeline_blocked_messages)",
            "pyrallel_pipeline_dispatch_capacity_blocked_messages",
            "pyrallel_pipeline_poll_records_total",
            "pyrallel_pipeline_poll_events_total",
        },
        "Pipeline worker capacity": {
            "pyrallel_pipeline_worker_capacity_units",
            "pyrallel_pipeline_subqueue_items",
            "pyrallel_pipeline_subqueues",
            "histogram_quantile(0.99, sum by (le, engine_type) (rate(pyrallel_pipeline_completion_to_commit_latency_seconds_bucket[1m])))",
        },
        "Pipeline settlement blockers": {"pyrallel_pipeline_settlement_blocker_state"},
        "Process-only support boundary": {
            "consumer_process_batch_transport_mode",
            "consumer_process_batch_support_state",
            "consumer_process_batch_timer_flush_supported",
            "consumer_process_batch_demand_flush_supported",
            "consumer_process_batch_recycle_supported",
        },
        "Process compatibility/reference metrics": {
            "consumer_process_batch_flush_count",
            "consumer_process_batch_avg_size",
            "consumer_process_batch_last_size",
            "consumer_process_batch_last_wait_seconds",
            "consumer_process_batch_buffered_items",
            "consumer_process_batch_buffered_age_seconds",
        },
        "Adaptive control caps": {
            "consumer_adaptive_backpressure_configured_max_in_flight",
            "consumer_adaptive_backpressure_effective_max_in_flight",
            "consumer_adaptive_backpressure_min_in_flight",
            "consumer_adaptive_concurrency_configured_max_in_flight",
            "consumer_adaptive_concurrency_effective_max_in_flight",
            "consumer_adaptive_concurrency_min_in_flight",
        },
    }

    for panel_title, expected_expressions in expected_expressions_by_panel.items():
        assert _panel_expressions(panels[panel_title]) == expected_expressions
        for target in panels[panel_title].get("targets", []):
            assert target.get("hide") is not True


def test_adaptive_catalog_splits_dense_parameters_without_selector() -> None:
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)
    expected_expressions_by_panel = {
        "Adaptive scale steps": {
            "consumer_adaptive_backpressure_scale_up_step",
            "consumer_adaptive_backpressure_scale_down_step",
            "consumer_adaptive_concurrency_scale_up_step",
            "consumer_adaptive_concurrency_scale_down_step",
        },
        "Adaptive cooldowns": {
            "consumer_adaptive_backpressure_cooldown_ms",
            "consumer_adaptive_concurrency_cooldown_ms",
        },
        "Adaptive thresholds and latency": {
            "consumer_adaptive_backpressure_lag_scale_up_threshold",
            "consumer_adaptive_backpressure_low_latency_threshold_ms",
            "consumer_adaptive_backpressure_high_latency_threshold_ms",
            "consumer_adaptive_backpressure_avg_completion_latency_seconds",
        },
    }

    assert "Adaptive detailed parameters" not in panels
    for panel_title, expected_expressions in expected_expressions_by_panel.items():
        panel = panels[panel_title]
        assert (
            _section_for_panel(dashboard, panel_title) == "Metric catalog / reference"
        )
        assert panel["type"] == "bargauge"
        assert _panel_expressions(panel) == expected_expressions
        assert len(panel["targets"]) <= 4


def test_process_compatibility_metrics_are_reference_not_operator_primary() -> None:
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)
    compatibility_panel = panels["Process compatibility/reference metrics"]
    compatibility_metrics = {
        "consumer_process_batch_flush_count",
        "consumer_process_batch_avg_size",
        "consumer_process_batch_last_size",
        "consumer_process_batch_last_wait_seconds",
        "consumer_process_batch_buffered_items",
        "consumer_process_batch_buffered_age_seconds",
    }

    assert (
        _section_for_panel(dashboard, "Process compatibility/reference metrics")
        == "Metric catalog / reference"
    )
    assert compatibility_metrics <= _panel_expressions(compatibility_panel)
    for metric_family in compatibility_metrics:
        for panel in dashboard["panels"]:
            if panel.get("type") == "row":
                continue
            if _section_for_panel(dashboard, panel["title"]) == "Operator overview":
                assert metric_family not in _panel_expressions(panel)


def test_reference_dashboard_separates_operator_triage_from_catalog() -> None:
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)

    assert "Reference / sample" in dashboard["title"]
    assert "Operator overview" in _section_titles_before_panel(
        dashboard, "Success throughput (1m rate)"
    )
    assert "Internal pipeline summary" in _section_titles_before_panel(
        dashboard, "Pipeline support matrix"
    )

    triage_panel = panels["Success throughput (1m rate)"]
    assert (
        _section_for_panel(dashboard, "Success throughput (1m rate)")
        == "Operator overview"
    )
    assert triage_panel["type"] == "timeseries"
    assert (
        'sum(rate(consumer_processed_total{status="success"}[1m]))'
        in _panel_expressions(triage_panel)
    )
    failure_panel = panels["Terminal failures (1m rate and 5m count)"]
    assert (
        'sum(rate(consumer_processed_total{status="failure"}[1m])) or vector(0)'
        in _panel_expressions(failure_panel)
    )
    assert (
        'sum(increase(consumer_processed_total{status="failure"}[5m])) or vector(0)'
        in _panel_expressions(failure_panel)
    )
    assert (
        '(sum(rate(consumer_processed_total{status="failure"}[1m])) or vector(0)) '
        "+ (sum(rate(consumer_commit_failures_total[1m])) or vector(0)) "
        "+ (sum(rate(consumer_dlq_publish_failures_total[1m])) or vector(0))"
        in _panel_expressions(failure_panel)
    )

    reference_panel = panels["Pipeline support matrix"]
    assert _section_for_panel(dashboard, "Pipeline support matrix") == (
        "Internal pipeline summary"
    )
    assert reference_panel["type"] == "stat"
    assert "pyrallel_pipeline_section_support_state" in _panel_expressions(
        reference_panel
    )
    assert "unsupported" in reference_panel.get("description", "").lower()
    assert "scrape" in reference_panel.get("description", "").lower()


def test_reference_dashboard_uses_requested_information_architecture_order() -> None:
    dashboard = _load_pyrallel_dashboard()
    section_order = [
        panel["title"]
        for panel in sorted(dashboard["panels"], key=_panel_sort_key)
        if panel.get("type") == "row"
    ]

    assert section_order == [
        "Operator overview",
        "Internal pipeline summary",
        "Process-only diagnostics",
        "Adaptive diagnostics",
        "Metric catalog / reference",
    ]
    assert _section_row_y(dashboard, "Metric catalog / reference") > _section_row_y(
        dashboard, "Adaptive diagnostics"
    )


def test_operator_overview_first_screen_has_compact_glanceable_panels() -> None:
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)
    first_row_titles = (
        "Success throughput (1m rate)",
        "Terminal failures (1m rate and 5m count)",
        "Backpressure current",
        "Resource status current",
    )
    second_row_titles = (
        "Processing latency percentiles (seconds)",
        "Queue, lag, and backlog",
    )

    for title in first_row_titles + second_row_titles:
        assert _section_for_panel(dashboard, title) == "Operator overview"

    first_row_x = [panels[title]["gridPos"]["x"] for title in first_row_titles]
    assert first_row_x == sorted(first_row_x)
    for title in ("Backpressure current", "Resource status current"):
        panel = panels[title]
        assert panel["type"] == "stat"
        assert panel["gridPos"]["w"] <= 4
        assert panel["gridPos"]["h"] <= 5
        for target in panel["targets"]:
            assert target["instant"] is True
            assert target["range"] is False


def test_operator_overview_does_not_include_catalog_or_large_no_data_panels() -> None:
    dashboard = _load_pyrallel_dashboard()
    catalog_y = _section_row_y(dashboard, "Metric catalog / reference")
    pipeline_titles = {
        "Pipeline support matrix",
        "Pipeline stage messages",
        "Pipeline blocked reasons",
        "Pipeline worker capacity",
        "Pipeline settlement blockers",
    }

    assert catalog_y > _section_row_y(dashboard, "Adaptive diagnostics")
    for panel in dashboard["panels"]:
        if panel.get("type") == "row":
            continue
        if _section_for_panel(dashboard, panel["title"]) == "Operator overview":
            assert panel["title"] not in pipeline_titles
            assert "support" not in panel["title"].lower()


def test_reference_dashboard_catalog_queries_live_public_metric_families() -> None:
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)
    expressions = _dashboard_expressions(dashboard)

    assert "pyrallel_pipeline_poll_records_total" in expressions
    assert "pyrallel_pipeline_poll_events_total" in expressions
    assert "pyrallel_pipeline_subqueue_items" in expressions
    assert "pyrallel_pipeline_subqueues" in expressions
    assert "pyrallel_pipeline_completion_to_commit_latency_seconds_bucket" in "\n".join(
        expressions
    )
    assert (
        "unsupported sections intentionally omit observed series"
        in panels["Pipeline support matrix"].get("description", "").lower()
    )


def test_reference_dashboard_uses_current_value_panel_for_pipeline_support_state() -> (
    None
):
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)
    support_panel = panels["Pipeline support matrix"]

    assert support_panel["type"] == "stat"
    target = _panel_targets_by_expr(support_panel)[
        "pyrallel_pipeline_section_support_state"
    ]
    assert target["instant"] is True
    assert target["range"] is False
    assert (
        "data does not have a time field"
        in support_panel.get("description", "").lower()
    )
    assert "supported" in support_panel.get("description", "").lower()
    assert "not_implemented" in support_panel.get("description", "").lower()


def test_reference_dashboard_classifies_pipeline_no_data_causes_in_panel_text() -> None:
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)

    cause_text_by_panel = {
        "Pipeline stage messages": (
            "supported sections emit zero-valued stage series",
            "get_pipeline_diagnostics publish path is not running",
        ),
        "Pipeline blocked reasons": (
            "supported sections emit zero-valued blocked reason series",
            "dispatch capacity only emits the active blocked reason",
        ),
        "Pipeline worker capacity": (
            "worker capacity is emitted when the workers section is supported",
            "engine_type is async or process",
        ),
        "Pipeline settlement blockers": (
            "supported healthy settlement emits all reasons as 0",
            "unsupported settlement relies on pipeline support matrix",
        ),
    }
    for title, required_fragments in cause_text_by_panel.items():
        description = panels[title].get("description", "").lower()
        for fragment in required_fragments:
            assert fragment in description


def test_reference_dashboard_splits_success_throughput_from_failure_signals() -> None:
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)

    success_panel = panels["Success throughput (1m rate)"]
    failure_panel = panels["Terminal failures (1m rate and 5m count)"]

    assert success_panel["type"] == "timeseries"
    assert failure_panel["type"] == "timeseries"
    assert _panel_expressions(success_panel) == {
        'sum(rate(consumer_processed_total{status="success"}[1m]))'
    }
    assert 'sum(rate(consumer_processed_total{status="success"}[1m]))' not in (
        _panel_expressions(failure_panel)
    )
    assert len(_panel_expressions(failure_panel)) >= 3
    assert "failure" in failure_panel.get("description", "").lower()
    assert "separate" in failure_panel.get("description", "").lower()


def test_reference_dashboard_sets_units_mappings_and_thresholds_for_status_panels() -> (
    None
):
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)

    backpressure_panel = panels["Backpressure current"]
    defaults = backpressure_panel["fieldConfig"]["defaults"]
    mapping_text = json.dumps(defaults.get("mappings", [])).lower()
    threshold_text = json.dumps(defaults.get("thresholds", {})).lower()
    assert backpressure_panel["type"] == "stat"
    assert defaults["min"] == 0
    assert defaults["max"] == 1
    assert "running" in mapping_text
    assert "paused" in mapping_text
    assert "green" in threshold_text
    assert "red" in threshold_text

    resource_panel = panels["Resource status current"]
    assert resource_panel["type"] == "stat"
    target = _panel_targets_by_expr(resource_panel)["consumer_resource_signal_status"]
    assert target["instant"] is True
    assert target["range"] is False
    assert "available" in resource_panel.get("description", "").lower()
    assert "stale" in resource_panel.get("description", "").lower()
    assert "unavailable" in resource_panel.get("description", "").lower()
    assert "first_sample_pending" in resource_panel.get("description", "").lower()


def test_reference_dashboard_does_not_graph_boolean_or_config_state() -> None:
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)

    state_or_config_panels = (
        "Backpressure current",
        "Adaptive scale steps",
        "Adaptive cooldowns",
        "Adaptive thresholds and latency",
        "Adaptive control caps",
        "Adaptive last decision",
        "Pipeline support matrix",
        "Pipeline settlement blockers",
        "Process-only support boundary",
    )
    for title in state_or_config_panels:
        assert panels[title]["type"] != "timeseries"


def test_reference_dashboard_uses_current_value_views_for_one_hot_state() -> None:
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)

    for title in (
        "Backpressure current",
        "Pipeline support matrix",
        "Pipeline settlement blockers",
        "Process-only support boundary",
        "Adaptive last decision",
        "Resource status current",
    ):
        assert panels[title]["type"] == "stat"
        for target in panels[title].get("targets", []):
            assert target["instant"] is True
            assert target["range"] is False


def test_reference_dashboard_plots_processing_latency_percentiles() -> None:
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)
    latency_panel = panels["Processing latency percentiles (seconds)"]

    assert latency_panel["type"] == "timeseries"
    expressions = _panel_expressions(latency_panel)
    assert (
        "histogram_quantile(0.50, sum by (le) "
        "(rate(consumer_processing_latency_seconds_bucket[1m])))"
    ) in expressions
    assert (
        "histogram_quantile(0.90, sum by (le) "
        "(rate(consumer_processing_latency_seconds_bucket[1m])))"
    ) in expressions
    assert (
        "histogram_quantile(0.99, sum by (le) "
        "(rate(consumer_processing_latency_seconds_bucket[1m])))"
    ) in expressions
    assert "p99" in {target["legendFormat"] for target in latency_panel["targets"]}
    assert "p90" in {target["legendFormat"] for target in latency_panel["targets"]}
    assert "p50" in {target["legendFormat"] for target in latency_panel["targets"]}
    assert latency_panel["fieldConfig"]["defaults"]["unit"] == "s"


def test_reference_dashboard_marks_process_only_and_pipeline_support_sections() -> None:
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)

    process_titles = (
        "Process-only route batch rate",
        "Process-only IPC payload efficiency",
        "Process-only route batch sizing",
        "Process-only worker-pipe timing",
        "Process-only support boundary",
    )
    for title in process_titles:
        panel = panels[title]
        description = panel.get("description", "").lower()
        assert "process" in title.lower() or "process-mode" in description
        assert "process-mode" in description
        assert "worker-pipes" in description or title == "Process-only support boundary"

    assert (
        "worker-pipes bypasses batchaccumulator flush counts"
        in panels["Process-only route batch rate"].get("description", "").lower()
    )

    support_panel_y = panels["Pipeline support matrix"]["gridPos"]["y"]
    observed_panel_titles = ("Pipeline stage messages", "Pipeline blocked reasons")
    for title in observed_panel_titles:
        assert panels[title]["gridPos"]["y"] > support_panel_y


def test_process_primary_panels_use_rates_for_cumulative_counters() -> None:
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)

    route_panel = panels["Process-only route batch rate"]
    payload_panel = panels["Process-only IPC payload efficiency"]
    size_panel = panels["Process-only route batch sizing"]

    assert _section_for_panel(dashboard, "Process-only route batch rate") == (
        "Process-only diagnostics"
    )
    assert "rate(consumer_process_route_batch_count[1m])" in _panel_expressions(
        route_panel
    )
    assert "rate(consumer_process_route_batch_items[1m])" in _panel_expressions(
        route_panel
    )
    for expr in _panel_expressions(route_panel) | _panel_expressions(payload_panel):
        assert expr not in {
            "consumer_process_route_batch_count",
            "consumer_process_route_batch_items",
            "consumer_process_completion_item_payload_count",
            "consumer_process_completion_batch_payload_count",
        }
    assert "rate(consumer_process_completion_item_payload_count[1m])" in (
        _panel_expressions(payload_panel)
    )
    assert "rate(consumer_process_completion_batch_payload_count[1m])" in (
        _panel_expressions(payload_panel)
    )
    assert "consumer_process_route_batch_avg_size" in _panel_expressions(size_panel)
    assert "consumer_process_route_batch_max_size" in _panel_expressions(size_panel)


def test_adaptive_primary_is_compact_and_details_are_reference_only() -> None:
    dashboard = _load_pyrallel_dashboard()
    panels = _dashboard_panels_by_title(dashboard)

    assert _section_for_panel(dashboard, "Adaptive last decision") == (
        "Adaptive diagnostics"
    )
    assert _section_for_panel(dashboard, "Adaptive control caps") == (
        "Adaptive diagnostics"
    )
    assert panels["Adaptive last decision"]["type"] == "stat"
    assert panels["Adaptive control caps"]["type"] == "bargauge"
    for title in (
        "Adaptive scale steps",
        "Adaptive cooldowns",
        "Adaptive thresholds and latency",
    ):
        assert panels[title]["type"] == "bargauge"
        assert _section_for_panel(dashboard, title) == "Metric catalog / reference"


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
        assert "Operator triage" in doc_text
        assert "curated subset" in doc_text
        assert "Metric catalog/reference" in doc_text
        assert "public metric surface" in doc_text


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
        assert "pyrallel_pipeline_settlement_blocker_state" in doc_text
        assert "pyrallel_pipeline_subqueue_items" in doc_text
        assert "pyrallel_pipeline_subqueues" in doc_text
        assert "pyrallel_pipeline_poll_records_total" in doc_text
        assert "pyrallel_pipeline_poll_events_total" in doc_text
        assert "pyrallel_pipeline_completion_to_commit_latency_seconds" in doc_text
        assert (
            "emitted alongside the sidecar projection" in doc_text
            or "sidecar projection과 함께 노출되는 BrokerPoller 소유 pipeline event metric"
            in doc_text
        )


def test_grafana_dashboard_includes_process_batch_panels() -> None:
    dashboard = _load_pyrallel_dashboard()
    panel_titles = {panel["title"] for panel in dashboard["panels"]}
    expressions = _dashboard_expressions(dashboard)

    assert "Process-only route batch rate" in panel_titles
    assert "Process-only IPC payload efficiency" in panel_titles
    assert "Process-only route batch sizing" in panel_titles
    assert "Process-only worker-pipe timing" in panel_titles
    assert "Process-only support boundary" in panel_titles
    assert "Adaptive control caps" in panel_titles
    assert "Adaptive scale steps" in panel_titles
    assert "Adaptive cooldowns" in panel_titles
    assert "Adaptive thresholds and latency" in panel_titles
    assert any("consumer_process_route_batch_count" in expr for expr in expressions)
    assert any("consumer_process_route_batch_items" in expr for expr in expressions)
    assert "consumer_process_route_batch_avg_size" in expressions
    assert "consumer_process_route_batch_max_size" in expressions
    assert "consumer_process_ipc_items_per_input_payload" in expressions
    assert "consumer_process_ipc_items_per_completion_payload" in expressions
    assert any(
        "consumer_process_completion_item_payload_count" in expr for expr in expressions
    )
    assert any(
        "consumer_process_completion_batch_payload_count" in expr
        for expr in expressions
    )
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
    panels = _dashboard_panels_by_title(dashboard)
    panel_titles = {panel["title"] for panel in dashboard["panels"]}
    expressions = _dashboard_expressions(dashboard)

    assert "Pipeline stage messages" in panel_titles
    assert "Pipeline blocked reasons" in panel_titles
    assert "Pipeline support matrix" in panel_titles
    assert "Pipeline settlement blockers" in panel_titles
    assert "Pipeline worker capacity" in panel_titles
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
    assert "pyrallel_pipeline_poll_records_total" in expressions
    assert "pyrallel_pipeline_poll_events_total" in expressions
    assert "pyrallel_pipeline_subqueue_items" in expressions
    assert "pyrallel_pipeline_subqueues" in expressions
    assert "pyrallel_pipeline_settlement_blocker_state" in expressions
    assert panels["Pipeline settlement blockers"]["type"] == "stat"
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
        assert "pyrallel_pipeline_settlement_blocker_state" in design_text
        assert "pyrallel_pipeline_subqueue_items" in design_text
        assert "pyrallel_pipeline_subqueues" in design_text
        assert "pyrallel_pipeline_poll_records_total" in design_text
        assert "pyrallel_pipeline_poll_events_total" in design_text
        assert "pyrallel_pipeline_completion_to_commit_latency_seconds" in design_text
        assert "pyrallel_pipeline_subqueue_items.state" in design_text
        assert "pyrallel_pipeline_subqueues.state" in design_text
        assert "pyrallel_pipeline_poll_events_total.event" in design_text
        assert "pyrallel_pipeline_poll_records_total.broker_kind" in design_text
        assert "`kafka`, `unknown`" in design_text
        assert (
            "pyrallel_pipeline_completion_to_commit_latency_seconds.engine_type"
            in design_text
        )
        assert (
            "broker-owned pipeline event metric emitted alongside the sidecar projection"
            in design_text
            or "sidecar projection과 함께 노출되는 BrokerPoller 소유 pipeline event metric"
            in design_text
        )
        assert "commit_pending" in design_text
        assert "dlq_publish_pending" in design_text
        assert "not_implemented" in design_text
        assert "unavailable" in design_text
        assert "top_k_loads" in design_text
        assert "top_k_depths" in design_text


def test_observability_design_docs_define_triage_first_metric_ownership() -> None:
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

    required_phrases = (
        "Triage-first metric model",
        "poll/acquire rate",
        "BrokerPoller/control-plane diagnostics",
        "queued and eligible",
        "WorkManager-owned",
        "dispatched is WorkManager-owned accepted submit accounting",
        "executing/admitted are engine-owned worker capacity diagnostics",
        "completed-unsettled and DLQ pending",
        "BrokerPoller settlement diagnostics",
        "completion-to-commit latency",
        "settlement-path diagnostic",
        "must not use Kafka broker timestamp as a substitute",
    )
    for design_path in design_paths:
        design_text = design_path.read_text(encoding="utf-8", errors="strict")
        for phrase in required_phrases:
            assert phrase in design_text


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


def test_operations_guides_prioritize_worker_pipes_process_metrics() -> None:
    guide_paths = [
        REPO_ROOT / "docs" / "operations" / "guide.en.md",
        REPO_ROOT / "docs" / "operations" / "guide.ko.md",
    ]

    required_phrases = (
        "worker-pipes bypasses BatchAccumulator flush counts",
        "consumer_process_route_batch_count",
        "consumer_process_route_batch_items",
        "consumer_process_ipc_items_per_input_payload",
        "consumer_process_ipc_items_per_completion_payload",
    )
    for guide_path in guide_paths:
        guide_text = guide_path.read_text(encoding="utf-8", errors="strict")
        for phrase in required_phrases:
            assert phrase in guide_text


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
