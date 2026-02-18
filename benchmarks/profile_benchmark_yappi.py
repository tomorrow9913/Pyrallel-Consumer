import pathlib
import sys

import yappi

from benchmarks.run_parallel_benchmark import main

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run_profile() -> None:
    sys.argv = [
        "profile_benchmark_yappi",
        "--num-messages",
        "20000",
        "--timeout-sec",
        "90",
        "--skip-baseline",
        "--skip-async",
        "--topic-prefix",
        "pyrallel-benchmark-prof",
        "--process-group",
        "process-benchmark-group-prof",
        "--bootstrap-servers",
        "localhost:9092",
    ]

    yappi.start(profile_threads=True)
    try:
        main()
    finally:
        yappi.stop()
        func_stats = yappi.get_func_stats()
        func_stats.sort("ttot", "desc")
        func_stats.print_all(out=sys.stdout)


if __name__ == "__main__":
    run_profile()
