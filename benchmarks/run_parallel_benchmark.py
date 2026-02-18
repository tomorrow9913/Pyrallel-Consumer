from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import List

from confluent_kafka.admin import AdminClient

from benchmarks.baseline_consumer import consume_messages
from benchmarks.kafka_admin import TopicConfig, reset_topics_and_groups
from benchmarks.producer import produce_messages
from benchmarks.pyrallel_consumer_test import ExecutionMode, run_pyrallel_consumer_test
from benchmarks.stats import BenchmarkResult, BenchmarkStats, write_results_json


def _check_kafka_connection(bootstrap_servers: str) -> None:
    client = AdminClient({"bootstrap.servers": bootstrap_servers})
    try:
        client.list_topics(timeout=5)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Failed to connect to Kafka at {bootstrap_servers}: {exc}"
        ) from exc


def _run_baseline_round(
    *,
    topic_name: str,
    num_messages: int,
    bootstrap_servers: str,
    num_partitions: int,
    num_keys: int,
    group_id: str,
) -> BenchmarkResult:
    produce_messages(
        num_messages=num_messages,
        num_keys=num_keys,
        num_partitions=num_partitions,
        topic_name=topic_name,
        bootstrap_servers=bootstrap_servers,
    )
    stats = BenchmarkStats(
        run_name="baseline",
        run_type="baseline",
        topic=topic_name,
        target_messages=num_messages,
    )
    result = consume_messages(
        num_messages_to_process=num_messages,
        bootstrap_servers=bootstrap_servers,
        topic_name=topic_name,
        group_id=group_id,
        stats=stats,
    )
    if result is None:
        result = stats.summary()
    return result


async def _run_pyrallel_round(
    *,
    topic_name: str,
    run_name: str,
    mode: ExecutionMode,
    num_messages: int,
    bootstrap_servers: str,
    num_partitions: int,
    num_keys: int,
    group_id: str,
    timeout_sec: int,
) -> BenchmarkResult:
    produce_messages(
        num_messages=num_messages,
        num_keys=num_keys,
        num_partitions=num_partitions,
        topic_name=topic_name,
        bootstrap_servers=bootstrap_servers,
    )
    stats = BenchmarkStats(
        run_name=run_name,
        run_type=mode,
        topic=topic_name,
        target_messages=num_messages,
    )
    timed_out, _, summary = await run_pyrallel_consumer_test(
        num_messages=num_messages,
        topic_name=topic_name,
        bootstrap_servers=bootstrap_servers,
        consumer_group=group_id,
        execution_mode=mode,
        num_partitions=num_partitions,
        stats_tracker=stats,
        timeout_sec=timeout_sec,
    )
    if timed_out:
        raise RuntimeError(
            f"Pyrallel consumer ({mode}) timed out before processing all messages"
        )
    if summary is None:
        summary = stats.summary()
    return summary


def _print_table(results: List[BenchmarkResult]) -> None:
    headers = ["Run", "Type", "Topic", "Messages", "TPS", "Avg ms", "P99 ms"]
    rows = [
        [
            result.run_name,
            result.run_type,
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Pyrallel throughput benchmarks")
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument("--num-messages", type=int, default=100_000)
    parser.add_argument("--num-keys", type=int, default=100)
    parser.add_argument("--num-partitions", type=int, default=8)
    parser.add_argument("--topic-prefix", default="pyrallel-benchmark")
    parser.add_argument("--baseline-group", default="baseline-benchmark-group")
    parser.add_argument("--async-group", default="async-benchmark-group")
    parser.add_argument("--process-group", default="process-benchmark-group")
    parser.add_argument(
        "--json-output",
        default=None,
        help="Path to write JSON summary (default benchmarks/results/<timestamp>.json)",
    )
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("--skip-async", action="store_true")
    parser.add_argument("--skip-process", action="store_true")
    parser.add_argument(
        "--skip-reset",
        action="store_true",
        help="Skip deleting/recreating topics and consumer groups before benchmarks",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=60,
        help="Timeout in seconds for each Pyrallel consumer run",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level for benchmark run",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    _check_kafka_connection(args.bootstrap_servers)

    topic_configs: dict[str, TopicConfig] = {}
    consumer_groups: list[str] = []
    if not args.skip_baseline:
        topic_configs[f"{args.topic_prefix}-baseline"] = TopicConfig(
            num_partitions=args.num_partitions
        )
        consumer_groups.append(args.baseline_group)
    if not args.skip_async:
        topic_configs[f"{args.topic_prefix}-async"] = TopicConfig(
            num_partitions=args.num_partitions
        )
        consumer_groups.append(args.async_group)
    if not args.skip_process:
        topic_configs[f"{args.topic_prefix}-process"] = TopicConfig(
            num_partitions=args.num_partitions
        )
        consumer_groups.append(args.process_group)

    if not topic_configs:
        raise RuntimeError("All benchmark runs are skipped; nothing to execute")

    if not args.skip_reset:
        unique_groups = list(dict.fromkeys(consumer_groups))
        print(
            "Resetting benchmark topics/groups: %s | groups=%s"
            % (", ".join(topic_configs.keys()), ", ".join(unique_groups))
        )
        reset_topics_and_groups(
            bootstrap_servers=args.bootstrap_servers,
            topics=topic_configs,
            consumer_groups=unique_groups,
        )

    results: List[BenchmarkResult] = []
    if not args.skip_baseline:
        topic_name = f"{args.topic_prefix}-baseline"
        results.append(
            _run_baseline_round(
                topic_name=topic_name,
                num_messages=args.num_messages,
                bootstrap_servers=args.bootstrap_servers,
                num_partitions=args.num_partitions,
                num_keys=args.num_keys,
                group_id=args.baseline_group,
            )
        )

    async def run_async_rounds() -> List[BenchmarkResult]:
        async_results: List[BenchmarkResult] = []
        if not args.skip_async:
            topic_name = f"{args.topic_prefix}-async"
            async_results.append(
                await _run_pyrallel_round(
                    topic_name=topic_name,
                    run_name="pyrallel-async",
                    mode="async",
                    num_messages=args.num_messages,
                    bootstrap_servers=args.bootstrap_servers,
                    num_partitions=args.num_partitions,
                    num_keys=args.num_keys,
                    group_id=args.async_group,
                    timeout_sec=args.timeout_sec,
                )
            )
        if not args.skip_process:
            topic_name = f"{args.topic_prefix}-process"
            async_results.append(
                await _run_pyrallel_round(
                    topic_name=topic_name,
                    run_name="pyrallel-process",
                    mode="process",
                    num_messages=args.num_messages,
                    bootstrap_servers=args.bootstrap_servers,
                    num_partitions=args.num_partitions,
                    num_keys=args.num_keys,
                    group_id=args.process_group,
                    timeout_sec=args.timeout_sec,
                )
            )
        return async_results

    results.extend(asyncio.run(run_async_rounds()))

    _print_table(results)
    output_path = args.json_output
    if output_path is None:
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        output_path = f"benchmarks/results/{timestamp}.json"
    write_results_json(results, Path(output_path))
    print(f"\nJSON summary written to {output_path}")


if __name__ == "__main__":
    main()
