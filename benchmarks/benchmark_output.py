from __future__ import annotations

from typing import List

from benchmarks.stats import BenchmarkResult


def print_table(results: List[BenchmarkResult]) -> None:
    headers = ["Run", "Type", "Order", "Topic", "Messages", "TPS", "Avg ms", "P99 ms"]
    rows = [
        [
            result.run_name,
            result.run_type,
            result.ordering,
            result.topic,
            f"{result.messages_processed:,}",
            f"{result.throughput_tps:,.2f}",
            f"{result.avg_processing_ms:.3f}",
            f"{result.p99_processing_ms:.3f}",
        ]
        for result in results
    ]
    widths = [
        max(len(headers[i]), max(len(row[i]) for row in rows))
        for i in range(len(headers))
    ]
    header_line = " | ".join(headers[i].ljust(widths[i]) for i in range(len(headers)))
    divider = "-+-".join("-" * widths[i] for i in range(len(headers)))
    print(header_line)
    print(divider)
    for row in rows:
        print(" | ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
