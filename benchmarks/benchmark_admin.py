from __future__ import annotations

from confluent_kafka.admin import AdminClient

from benchmarks.kafka_admin import TopicConfig, reset_topics_and_groups


def check_kafka_connection(bootstrap_servers: str) -> None:
    client = AdminClient({"bootstrap.servers": bootstrap_servers})
    try:
        client.list_topics(timeout=5)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Failed to connect to Kafka at {bootstrap_servers}: {exc}"
        ) from exc


def reset_run_targets(
    *,
    bootstrap_servers: str,
    topic_name: str,
    group_id: str,
    num_partitions: int,
) -> None:
    print("Resetting benchmark topics/groups: %s | groups=%s" % (topic_name, group_id))
    reset_topics_and_groups(
        bootstrap_servers=bootstrap_servers,
        topics={topic_name: TopicConfig(num_partitions=num_partitions)},
        consumer_groups=[group_id],
    )
