import argparse
import json
import time
from collections import defaultdict
from typing import Any, Dict

from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic

# Kafka 설정 (단일 브로커용)
conf: Dict[str, Any] = {
    "bootstrap.servers": "localhost:9092",
    "client.id": "test-data-producer",
    "linger.ms": 10,
    "compression.type": "lz4",
    "batch.size": 65536,
}

DEFAULT_TOPIC = "test_topic"


def create_topic_if_not_exists(
    admin_conf: Dict[str, Any], topic_name: str, num_partitions: int
) -> None:
    """주어진 이름과 파티션 개수로 Kafka 토픽이 없으면 생성합니다."""
    admin_client = AdminClient({"bootstrap.servers": admin_conf["bootstrap.servers"]})
    try:
        new_topics = [
            NewTopic(topic_name, num_partitions=num_partitions, replication_factor=1)
        ]
        futures = admin_client.create_topics(new_topics)
        for tp, future in futures.items():
            try:
                future.result()
                print(f"Topic '{tp}' created.")
            except Exception as e:
                if "TOPIC_ALREADY_EXISTS" in str(e):
                    print(f"Topic '{tp}' already exists.")
                else:
                    print(f"Failed to create topic {tp}: {e}")
    except Exception as e:
        print(f"Error checking/creating topic: {e}")


def produce_messages(
    num_messages: int,
    num_keys: int,
    num_partitions: int,
    topic_name: str,
) -> None:
    """지정된 수의 메시지와 키를 사용하여 테스트 데이터를 생성하고 Kafka에 전송합니다."""
    producer = Producer(conf)
    create_topic_if_not_exists(conf, topic_name, num_partitions=num_partitions)

    print(f"Starting to produce {num_messages} messages with {num_keys} unique keys...")
    start_time = time.time()

    sequences: Dict[str, int] = defaultdict(int)
    for i in range(num_messages):
        key_id = i % num_keys
        key = f"key-{key_id}"

        payload = {
            "key": key,
            "sequence": sequences[key],
            "timestamp": time.time_ns(),
        }

        sequences[key] += 1

        message_json = json.dumps(payload)

        try:
            producer.produce(
                topic_name,
                key=key.encode("utf-8"),
                value=message_json.encode("utf-8"),
            )
        except BufferError:
            print("Queue full. Flushing...")
            producer.flush()
            producer.produce(
                topic_name,
                key=key.encode("utf-8"),
                value=message_json.encode("utf-8"),
            )

        if i > 0 and (i + 1) % 10000 == 0:
            producer.poll(0)  # Handle delivery reports
            elapsed = time.time() - start_time
            print(f"Produced {i + 1}/{num_messages}... ({(i + 1) / elapsed:.2f} msg/s)")

    print("Flushing remaining messages...")
    producer.flush(timeout=60)

    end_time = time.time()
    total_time = end_time - start_time
    print("\n--- Production Summary ---")
    print(f"Total: {num_messages} events")
    print(f"Time: {total_time:.2f}s")
    print(f"Avg TPS: {num_messages / total_time:.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kafka Test Data Producer")
    parser.add_argument(
        "--num-messages",
        type=int,
        default=50000,
        help="Total number of messages to produce.",
    )
    parser.add_argument(
        "--num-keys",
        type=int,
        default=100,
        help="Number of unique keys to cycle through.",
    )
    parser.add_argument(
        "--num-partitions",
        type=int,
        default=8,
        help="Number of partitions for the topic.",
    )
    parser.add_argument(
        "--topic",
        type=str,
        default=DEFAULT_TOPIC,
        help="Topic name to produce to.",
    )
    args = parser.parse_args()

    produce_messages(
        num_messages=args.num_messages,
        num_keys=args.num_keys,
        num_partitions=args.num_partitions,
        topic_name=args.topic,
    )
