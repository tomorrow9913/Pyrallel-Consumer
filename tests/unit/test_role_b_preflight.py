from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

role_b_preflight = importlib.import_module("scripts.role_b_preflight")


def test_build_commands_uses_expected_default_targets() -> None:
    commands = role_b_preflight.build_commands(python_executable="python")

    assert commands == [
        ["python", "-m", "pytest", "tests/unit/test_role_b_preflight.py", "-q"],
        [
            "python",
            "-m",
            "pytest",
            "tests/unit/benchmarks/test_benchmark_runtime.py",
            "-q",
        ],
        ["python", "-m", "pytest", "tests/unit/test_release_policy.py", "-q"],
    ]


def test_format_commands_outputs_ordered_shell_lines() -> None:
    rendered = role_b_preflight.format_commands(
        [
            ["python", "-m", "pytest", "tests/unit/a.py", "-q"],
            ["python", "-m", "pytest", "tests/unit/b.py", "-q"],
        ]
    )

    assert rendered == (
        "1. python -m pytest tests/unit/a.py -q\n"
        "2. python -m pytest tests/unit/b.py -q"
    )


def test_run_commands_stops_on_first_failure() -> None:
    calls: list[list[str]] = []

    def _runner(command: list[str], *, check: bool, text: bool) -> SimpleNamespace:
        del check, text
        calls.append(command)
        if len(calls) == 2:
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    exit_code = role_b_preflight.run_commands(
        [
            ["python", "-m", "pytest", "tests/unit/a.py", "-q"],
            ["python", "-m", "pytest", "tests/unit/b.py", "-q"],
            ["python", "-m", "pytest", "tests/unit/c.py", "-q"],
        ],
        runner=_runner,
        continue_on_failure=False,
    )

    assert exit_code == 1
    assert calls == [
        ["python", "-m", "pytest", "tests/unit/a.py", "-q"],
        ["python", "-m", "pytest", "tests/unit/b.py", "-q"],
    ]


def test_run_commands_continues_when_requested() -> None:
    calls: list[list[str]] = []

    def _runner(command: list[str], *, check: bool, text: bool) -> SimpleNamespace:
        del check, text
        calls.append(command)
        return SimpleNamespace(returncode=2 if len(calls) == 1 else 0)

    exit_code = role_b_preflight.run_commands(
        [
            ["python", "-m", "pytest", "tests/unit/a.py", "-q"],
            ["python", "-m", "pytest", "tests/unit/b.py", "-q"],
        ],
        runner=_runner,
        continue_on_failure=True,
    )

    assert exit_code == 2
    assert calls == [
        ["python", "-m", "pytest", "tests/unit/a.py", "-q"],
        ["python", "-m", "pytest", "tests/unit/b.py", "-q"],
    ]


def test_main_lists_commands_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = role_b_preflight.main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Role B preflight command set:" in captured.out
    assert "tests/unit/test_role_b_preflight.py" in captured.out


def test_main_run_invokes_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        role_b_preflight,
        "build_commands",
        lambda: [["python", "-m", "pytest", "tests/unit/a.py", "-q"]],
    )
    monkeypatch.setattr(
        role_b_preflight,
        "run_commands",
        lambda commands, continue_on_failure=False: 0,
    )

    assert role_b_preflight.main(["--run"]) == 0
