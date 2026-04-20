from __future__ import annotations

import argparse
import shlex

# Uses subprocess to execute fixed local pytest commands.
import subprocess  # nosec B404
import sys
from collections.abc import Callable, Sequence

DEFAULT_TEST_TARGETS: tuple[str, ...] = (
    "tests/unit/test_role_b_preflight.py",
    "tests/unit/benchmarks/test_benchmark_runtime.py",
    "tests/unit/test_release_policy.py",
)


def build_commands(
    *,
    python_executable: str | None = None,
    test_targets: Sequence[str] = DEFAULT_TEST_TARGETS,
) -> list[list[str]]:
    python_cmd = python_executable or sys.executable
    return [[python_cmd, "-m", "pytest", target, "-q"] for target in test_targets]


def format_commands(commands: Sequence[Sequence[str]]) -> str:
    lines: list[str] = []
    for index, command in enumerate(commands, start=1):
        rendered = " ".join(shlex.quote(part) for part in command)
        lines.append(f"{index}. {rendered}")
    return "\n".join(lines)


def run_commands(
    commands: Sequence[Sequence[str]],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    continue_on_failure: bool = False,
) -> int:
    exit_code = 0
    for command in commands:
        completed = runner(
            list(command),
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            exit_code = completed.returncode
            if not continue_on_failure:
                break
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Role B lane preflight validation command runner."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the canonical validation command list.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute canonical validation commands.",
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Continue running later commands even if an earlier command fails.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    commands = build_commands()

    if args.list or not args.run:
        print("Role B preflight command set:")
        print(format_commands(commands))

    if not args.run:
        return 0

    return run_commands(commands, continue_on_failure=args.continue_on_failure)


if __name__ == "__main__":
    raise SystemExit(main())
