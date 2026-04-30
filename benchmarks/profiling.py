from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from pyrallel_consumer.dto import WorkItem


@contextmanager
def profile_session(
    *,
    enabled: bool,
    run_name: str,
    output_dir: Path,
    clock: str,
    profile_threads: bool,
    profile_greenlets: bool,
    top_n: int,
):
    if not enabled:
        yield
        return
    try:
        import yappi  # type: ignore[import-untyped]
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError("yappi is required for profiling; install dev deps") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    yappi.set_clock_type(clock)
    yappi.start(profile_threads=profile_threads, profile_greenlets=profile_greenlets)
    try:
        yield
    finally:
        yappi.stop()
        stats = yappi.get_func_stats()
        prof_path = output_dir / f"{run_name}.prof"
        stats.save(str(prof_path), type="pstat")
        print(f"\n[profile] saved to {prof_path}")
        if top_n > 0:
            print(f"\nTop {top_n} functions by total time [{run_name}]\n")
            stats.sort("ttot")
            top_stats: Any = stats[:top_n]
            print(format_stats_table(top_stats, limit=top_n))
        yappi.clear_stats()


def stop_yappi_worker(path: Path) -> None:
    try:
        import yappi

        yappi.stop()
        stats = yappi.get_func_stats()
        stats.save(str(path), type="pstat")
        yappi.clear_stats()
        print(f"[profile worker] saved to {path}")
    except Exception:  # noqa: BLE001
        # Worker teardown should not crash the process if profiling fails.
        return


def format_stats_table(stats: Iterable[Any], *, limit: int) -> str:
    rows: list[tuple[str, int, float, float]] = []
    for entry in list(stats)[:limit]:
        name = entry.full_name if hasattr(entry, "full_name") else str(entry)
        ncall = getattr(entry, "ncall", 0)
        ttot = getattr(entry, "ttot", 0.0)
        tavg = getattr(entry, "tavg", 0.0)
        rows.append((name, ncall, ttot, tavg))
    if not rows:
        return "(no stats)"
    col_widths = [
        max(len("Function"), max(len(r[0]) for r in rows)),
        max(len("Calls"), max(len(str(r[1])) for r in rows)),
        len("Total(s)"),
        len("Avg(s)"),
    ]
    header = ["Function", "Calls", "Total(s)", "Avg(s)"]
    lines = [
        " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(header)),
        "-+-".join("-" * col_widths[i] for i in range(len(header))),
    ]
    for name, calls, ttot, tavg in rows:
        lines.append(
            " | ".join(
                [
                    name.ljust(col_widths[0]),
                    str(calls).rjust(col_widths[1]),
                    f"{ttot:.6f}".rjust(col_widths[2]),
                    f"{tavg:.6f}".rjust(col_widths[3]),
                ]
            )
        )
    return "\n".join(lines)


def summarize_worker_profiles(
    run_name: str, profile_dir: Path, top_n: int, clock: str
) -> None:
    del clock
    try:
        import yappi
    except Exception:  # noqa: BLE001
        return

    paths = list(profile_dir.glob(f"{run_name}-worker-*.prof"))
    if not paths:
        return

    merged = yappi.YFuncStats()
    for path in paths:
        try:
            merged.add(str(path))
        except Exception:  # noqa: BLE001
            continue

    merged_path = profile_dir / f"{run_name}-workers-merged.prof"
    merged.save(str(merged_path), type="pstat")
    print(
        f"[profile workers] merged stats saved to {merged_path} ({len(paths)} workers)"
    )

    if top_n > 0:
        merged.sort("ttot")
        print(f"\nTop {top_n} functions by total time [{run_name} workers]\n")
        print(format_stats_table(merged, limit=top_n))


def wrap_process_worker_for_profile(
    worker_fn: Callable[[WorkItem], None],
    *,
    output_dir: Path,
    run_name: str,
    clock: str,
    profile_threads: bool,
    profile_greenlets: bool,
) -> Callable[[WorkItem], None]:
    del output_dir, run_name, clock, profile_threads, profile_greenlets
    # Worker profiling disabled: yappi is unstable in worker processes and emits
    # internal errors.
    return worker_fn


PYSPY_FORMAT_EXTENSIONS: dict[str, str] = {
    "flamegraph": ".svg",
    "speedscope": ".json",
    "chrometrace": ".json",
    "raw": ".txt",
}


def relaunch_with_pyspy(args: argparse.Namespace) -> int:
    """Re-execute the benchmark script under py-spy."""
    py_spy_bin = shutil.which("py-spy")
    if py_spy_bin is None:
        raise RuntimeError(
            "py-spy not found on PATH. Install it via: uv add --dev py-spy"
        )

    output_dir = Path(args.py_spy_output)
    output_dir.mkdir(parents=True, exist_ok=True)

    child_argv: list[str] = [sys.executable, "-m", "benchmarks.run_parallel_benchmark"]
    skip_next = False
    for arg in args._raw_argv:
        if skip_next:
            skip_next = False
            continue
        if (
            arg == "--py-spy"
            or arg == "--py-spy-native"
            or arg == "--py-spy-idle"
            or arg == "--py-spy-top"
        ):
            continue
        if arg.startswith("--py-spy-format"):
            if "=" not in arg:
                skip_next = True
            continue
        if arg.startswith("--py-spy-output"):
            if "=" not in arg:
                skip_next = True
            continue
        if arg.startswith("--py-spy-rate"):
            if "=" not in arg:
                skip_next = True
            continue
        child_argv.append(arg)
    child_argv.append("--_pyspy-child")

    if args.py_spy_top:
        cmd: list[str] = [py_spy_bin, "top", "--subprocesses"]
        cmd.extend(["--rate", str(args.py_spy_rate)])
        if args.py_spy_native:
            cmd.append("--native")
        if args.py_spy_idle:
            cmd.append("--idle")
        cmd.append("--")
        cmd.extend(child_argv)
        print(f"[py-spy top] {' '.join(cmd)}")
        result = subprocess.run(cmd)
        return result.returncode

    fmt = args.py_spy_format
    ext = PYSPY_FORMAT_EXTENSIONS.get(fmt, ".svg")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_file = output_dir / f"pyspy-{fmt}-{timestamp}{ext}"

    cmd = [
        py_spy_bin,
        "record",
        "--subprocesses",
        "--format",
        fmt,
        "--output",
        str(output_file),
        "--rate",
        str(args.py_spy_rate),
    ]
    if args.py_spy_native:
        cmd.append("--native")
    if args.py_spy_idle:
        cmd.append("--idle")
    cmd.append("--")
    cmd.extend(child_argv)

    print(f"[py-spy record] {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode == 0:
        print(f"\n[py-spy] profile saved to {output_file}")
    else:
        print(f"\n[py-spy] py-spy exited with code {result.returncode}")
    return result.returncode
