"""Regression guards for execution-engine transport invariants."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTROL_PLANE_ROOT = REPO_ROOT / "pyrallel_consumer" / "control_plane"
FORBIDDEN_TRANSPORT_TERMS = (
    "transport_mode",
    "worker_pipes",
    "shared_queue",
    "ProcessExecutionEngine",
)
ALLOWED_ENGINE_METHODS = {
    "submit",
    "poll_completed_events",
    "wait_for_completion",
    "get_in_flight_count",
    "get_runtime_metrics",
    "shutdown",
}
ENGINE_ATTRIBUTE_ALIASES = {"execution_engine", "_execution_engine"}


def _iter_control_plane_modules() -> list[Path]:
    return sorted(CONTROL_PLANE_ROOT.glob("*.py"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _collect_execution_engine_method_usage(path: Path) -> set[str]:
    tree = ast.parse(_read_text(path), filename=str(path))
    methods: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        base = node.value
        if isinstance(base, ast.Name) and base.id in ENGINE_ATTRIBUTE_ALIASES:
            methods.add(node.attr)
            continue
        if not isinstance(base, ast.Attribute):
            continue
        if not isinstance(base.value, ast.Name):
            continue
        if base.value.id != "self":
            continue
        if base.attr not in ENGINE_ATTRIBUTE_ALIASES:
            continue
        methods.add(node.attr)

    return methods


def test_control_plane_source_does_not_reference_process_transport_details() -> None:
    offenders: list[str] = []

    for path in _iter_control_plane_modules():
        text = _read_text(path)
        for term in FORBIDDEN_TRANSPORT_TERMS:
            if term in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} -> {term}")

    assert offenders == []


def test_control_plane_only_uses_base_execution_engine_contract_methods() -> None:
    used_methods: set[str] = set()

    for path in _iter_control_plane_modules():
        used_methods.update(_collect_execution_engine_method_usage(path))

    assert used_methods <= ALLOWED_ENGINE_METHODS
    assert used_methods >= {
        "submit",
        "poll_completed_events",
        "wait_for_completion",
        "get_runtime_metrics",
    }
