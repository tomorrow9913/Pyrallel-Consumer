from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_e2e_compose_includes_prometheus_and_grafana_services() -> None:
    compose_text = (REPO_ROOT / ".github" / "e2e.compose.yml").read_text()

    assert "kafka-exporter:" in compose_text
    assert "restart: unless-stopped" in compose_text
    assert "prometheus:" in compose_text
    assert "grafana:" in compose_text
    assert "../monitoring/prometheus.yml" in compose_text
    assert "../monitoring/grafana/provisioning" in compose_text
    assert "../monitoring/grafana/dashboards" in compose_text


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


def test_grafana_dashboard_includes_process_batch_panels() -> None:
    dashboard = json.loads(
        (
            REPO_ROOT
            / "monitoring"
            / "grafana"
            / "dashboards"
            / "pyrallel-overview.json"
        ).read_text()
    )
    panel_titles = {panel["title"] for panel in dashboard["panels"]}
    expressions = {
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        if "expr" in target
    }

    assert "Process batch flushes" in panel_titles
    assert "Process batch sizing" in panel_titles
    assert "Process batch timing" in panel_titles
    assert 'consumer_process_batch_flush_count{reason="timer"}' in expressions
    assert "consumer_process_batch_avg_size" in expressions
    assert "consumer_process_batch_avg_main_to_worker_ipc_seconds" in expressions
    assert "consumer_process_batch_avg_worker_exec_seconds" in expressions
    assert "consumer_process_batch_avg_worker_to_main_ipc_seconds" in expressions


def test_monitoring_ci_workflow_runs_prometheus_and_grafana_smoke_checks() -> None:
    workflow_text = (
        REPO_ROOT / ".github" / "workflows" / "ci_monitoring.yml"
    ).read_text()

    assert (
        "docker compose -f .github/e2e.compose.yml up -d kafka-1 kafka-exporter prometheus grafana"
        in workflow_text
    )
    assert "http://127.0.0.1:9090/-/ready" in workflow_text
    assert "http://127.0.0.1:3000/api/health" in workflow_text
    assert "http://127.0.0.1:9091/metrics" in workflow_text
    assert "http://127.0.0.1:9090/api/v1/targets" in workflow_text
    assert "http://127.0.0.1:3000/api/datasources/uid/prometheus" in workflow_text
    assert "http://127.0.0.1:3000/api/search?query=Pyrallel" in workflow_text
