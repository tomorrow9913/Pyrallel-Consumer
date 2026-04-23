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
    assert "from confluent_kafka.admin import AdminClient" in workflow_text
    assert "client.list_topics(timeout=5)" in workflow_text
    assert "--num-messages 4000" in workflow_text
    assert "--timeout-sec 180" in workflow_text
