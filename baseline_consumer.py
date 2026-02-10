# baseline_consumer.py
import sys
import time
from typing import Any, Dict

from confluent_kafka import Consumer, KafkaException
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic  # Corrected import for NewTopic

# Kafka configuration for KRAFT mode
conf: Dict[str, Any] = {
    "bootstrap.servers": "localhost:9092",  # Connect to Kafka running locally via Docker Compose (external listener)
    "group.id": "baseline_consumer_group",
    "auto.offset.reset": "earliest",  # Start from the beginning if no offset is committed
    "enable.auto.commit": False,  # Manually commit offsets for better control
    "session.timeout.ms": 6000,
    "max.poll.interval.ms": 300000,
    "isolation.level": "read_committed",  # Important for transactional semantics
}

topic = "test_topic"


def delivery_report(err, msg):
    """Called once for each message produced to indicate delivery result."""
    if err is not None:
        print(f"Message delivery failed: {err}")


def create_topic_if_not_exists(admin_conf, topic_name):
    """Ensures the Kafka topic exists using AdminClient."""
    admin_client = AdminClient(admin_conf)
    try:
        print(f"Attempting to create topic '{topic_name}'...")
        # Note: Replication factor and partitions might need adjustment for multi-broker clusters
        new_topics = [NewTopic(topic_name, num_partitions=1, replication_factor=1)]
        futures = admin_client.create_topics(new_topics)

        for tp, future in futures.items():
            try:
                future.result()
                print(f"Topic '{tp}' created successfully.")
            except Exception as e:
                if "topic already exists" in str(e).lower():
                    print(f"Topic '{tp}' already exists.")
                else:
                    print(f"Failed to create topic {tp}: {e}")
    except Exception as e:
        print(f"An error occurred during topic check/creation: {e}")


def consume_messages(num_messages_to_process=None):
    consumer = Consumer(conf)

    consumer.subscribe([topic])
    print(f"Starting baseline consumer for topic '{topic}'.")
    print(
        f"Will process up to {num_messages_to_process} messages if specified, otherwise indefinitely."
    )

    messages_processed = 0
    start_time = time.time()

    try:
        while True:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                if (
                    num_messages_to_process is not None
                    and messages_processed >= num_messages_to_process
                ):
                    print(
                        f"Processed {messages_processed} messages. Reached target. Exiting."
                    )
                    break
                continue

            if msg.error():
                # Access error codes via public constants
                if msg.error().code() == KafkaException.PARTITION_EOF:
                    sys.stderr.write(
                        "%% %s [%s] reached end at offset %d\n"
                        % (msg.topic(), msg.partition(), msg.offset())
                    )
                elif (
                    msg.error().code()
                    == KafkaException.TRANSACTIONAL_ID_AUTHORIZATION_FAILED
                ):
                    print(
                        "Transaction authorization failed. Is transaction.id configured correctly?"
                    )
                    break
                else:
                    sys.stderr.write(f"%% Message error: {msg.error()}\n")
                continue

            # Ensure msg.value() is not None before decoding
            if msg.value() is None:
                print(
                    f"Received message with None value at offset {msg.offset()}. Skipping."
                )
                continue

            msg.value().decode("utf-8")
            # Simulate message processing time
            # asyncio.sleep(0.0001) # Note: consumer.poll is synchronous, so asyncio.sleep is not appropriate here.

            messages_processed += 1

            # Manual commit strategy: commit periodically
            if messages_processed % 100 == 0:  # Commit every 100 messages
                consumer.commit(asynchronous=True)  # Asynchronous commit
                elapsed_time = time.time() - start_time
                tps = messages_processed / elapsed_time if elapsed_time > 0 else 0
                print(
                    f"Processed {messages_processed} messages. Committed offsets. Current TPS: {tps:.2f}"
                )

            if (
                num_messages_to_process is not None
                and messages_processed >= num_messages_to_process
            ):
                print(
                    f"Reached target of {num_messages_to_process} messages. Committing final offsets."
                )
                consumer.commit(asynchronous=True)
                break

    except KeyboardInterrupt:
        print("Consumer interrupted by user.")
    finally:
        # Ensure final commit and close the consumer
        print("Committing final offsets and closing consumer...")
        consumer.commit(asynchronous=False)  # Synchronous commit for finalization
        consumer.close()
        end_time = time.time()
        print(
            f"Consumer closed. Total messages processed (approx): {messages_processed}"
        )
        if end_time > start_time:
            runtime = end_time - start_time
            tps_final = messages_processed / runtime if runtime > 0 else 0
            print(f"Total runtime: {runtime:.2f} seconds")
            print(f"Final TPS: {tps_final:.2f}")


if __name__ == "__main__":
    NUM_MESSAGES_TO_PROCESS = 50000

    create_topic_if_not_exists(conf, topic)

    consume_messages(num_messages_to_process=NUM_MESSAGES_TO_PROCESS)
