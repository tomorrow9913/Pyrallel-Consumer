from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from confluent_kafka.admin import AdminClient

BOOTSTRAP_SERVERS = "127.0.0.1:9092"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _strict_e2e_gate() -> bool:
    return os.environ.get("PYRALLEL_E2E_REQUIRE_BROKER", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _skip_or_fail(message: str) -> None:
    if _strict_e2e_gate():
        pytest.fail(message)
    pytest.skip(message)


def _wait_for_kafka_metadata(timeout_sec: float) -> None:
    client = AdminClient(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "socket.timeout.ms": 1000,
        }
    )
    deadline = time.monotonic() + timeout_sec
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            metadata = client.list_topics(timeout=5)
            if getattr(metadata, "brokers", None):
                return
        except Exception as exc:
            last_error = exc
        time.sleep(1)
    raise RuntimeError(
        f"{BOOTSTRAP_SERVERS} did not answer Kafka metadata requests: {last_error}"
    )


def _fetch_json(url: str, auth: str | None = None) -> Any:
    request = urllib.request.Request(url)
    if auth is not None:
        request.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def _fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read().decode()


def _wait_until(description: str, timeout_sec: float, predicate) -> None:
    deadline = time.monotonic() + timeout_sec
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"{description} did not become ready: {last_error}")


def _prometheus_target_health() -> dict[str, str]:
    """Return Prometheus active target health keyed by job name."""
    return {
        item["labels"].get("job"): item["health"]
        for item in _fetch_json("http://127.0.0.1:9090/api/v1/targets")["data"][
            "activeTargets"
        ]
    }


def test_monitoring_stack_scrapes_consumer_and_provisions_grafana(
    tmp_path: Path,
) -> None:
    try:
        _wait_for_kafka_metadata(timeout_sec=90 if _strict_e2e_gate() else 5)
        _wait_until(
            "Prometheus",
            timeout_sec=90,
            predicate=lambda: _fetch_text("http://127.0.0.1:9090/-/ready") != "",
        )
        grafana_auth = base64.b64encode(b"admin:local-e2e").decode()
        _wait_until(
            "Grafana",
            timeout_sec=90,
            predicate=lambda: (
                _fetch_json(
                    "http://127.0.0.1:3000/api/health",
                    auth=grafana_auth,
                ).get("database")
                == "ok"
            ),
        )
        try:
            _wait_until(
                "Prometheus kafka-exporter target",
                timeout_sec=90,
                predicate=lambda: (
                    _prometheus_target_health().get("kafka-exporter") == "up"
                ),
            )
        except RuntimeError as exc:
            _skip_or_fail(
                "Monitoring stack Prometheus kafka-exporter target is not healthy: "
                f"{exc}"
            )
    except Exception as exc:
        _skip_or_fail(f"Monitoring stack not available for e2e smoke test: {exc}")

    json_output = tmp_path / "ci-monitoring.json"
    command = [
        sys.executable,
        "-m",
        "benchmarks.run_parallel_benchmark",
        "--skip-baseline",
        "--skip-async",
        "--workloads",
        "sleep",
        "--order",
        "partition",
        "--num-messages",
        "8000",
        "--num-keys",
        "200",
        "--num-partitions",
        "4",
        "--worker-sleep-ms",
        "10",
        "--timeout-sec",
        "180",
        "--metrics-port",
        "9091",
        "--topic-prefix",
        f"ci-monitoring-{int(time.time())}",
        "--json-output",
        str(json_output),
    ]
    benchmark = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_until(
            "benchmark metrics endpoint",
            timeout_sec=180,
            predicate=lambda: (
                "consumer_processed_total"
                in _fetch_text("http://127.0.0.1:9091/metrics")
                and "consumer_in_flight_count"
                in _fetch_text("http://127.0.0.1:9091/metrics")
            ),
        )
        try:
            _wait_until(
                "Prometheus targets",
                timeout_sec=180,
                predicate=lambda: (
                    _prometheus_target_health().get("kafka-exporter") == "up"
                    and _prometheus_target_health().get("pyrallel-consumer") == "up"
                ),
            )
        except RuntimeError as exc:
            _skip_or_fail(
                "Monitoring stack Prometheus targets not healthy for e2e smoke "
                f"test: {exc}"
            )
        try:
            _wait_until(
                "Grafana provisioning",
                timeout_sec=60,
                predicate=lambda: (
                    _fetch_json(
                        "http://127.0.0.1:3000/api/datasources/uid/prometheus",
                        auth=grafana_auth,
                    ).get("uid")
                    == "prometheus"
                    and any(
                        result.get("title") == "Pyrallel Overview"
                        for result in _fetch_json(
                            "http://127.0.0.1:3000/api/search?query=Pyrallel",
                            auth=grafana_auth,
                        )
                    )
                ),
            )
        except RuntimeError as exc:
            _skip_or_fail(
                "Monitoring stack Grafana provisioning is not healthy for e2e "
                f"smoke test: {exc}"
            )
        output, _ = benchmark.communicate(timeout=220)
        assert benchmark.returncode == 0, output
        assert json_output.exists()
    finally:
        if benchmark.poll() is None:
            benchmark.terminate()
            try:
                benchmark.wait(timeout=10)
            except subprocess.TimeoutExpired:
                benchmark.kill()
