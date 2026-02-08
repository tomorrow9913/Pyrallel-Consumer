# ! /usr/bin/env python3
# -*- coding: utf-8 -*-
# source: main.py
from confluent_kafka import Consumer, Producer

producer = Producer({"bootstrap.servers": "localhost:9092"})
consumer = Consumer(
    {
        "bootstrap.servers": "localhost:9092",
        "group.id": "my_group",
        "auto.offset.reset": "earliest",
    }
)

test_data = {
    "key1": "fBpO3rLva3LDS7ZYbslCtUdh0S1cWwjP",
    "key2": "fBpO3rLva3LDS7ZYbslCtUdh0S1cWwjP",
    "key3": "fBpO3rLva3LDS7ZYbslCtUdh0S1cWwjP",
}


def produce_messages(num_messages):
    """Placeholder function for producing messages.
    In a real scenario, this would produce actual Kafka messages.
    """
    print(f"Producing {num_messages} messages (placeholder).")


def main():
    produce_messages(10)
    consumer.subscribe(["test_topic"])
    msg_count = 0
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                print(f"Consumer error: {msg.error()}")
                continue
            print(
                f"Received message: {msg.key().decode('utf-8')}:{msg.value().decode('utf-8')}"
            )
            msg_count += 1
            if msg_count >= 30:
                break
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
