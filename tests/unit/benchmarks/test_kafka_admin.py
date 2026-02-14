from __future__ import annotations

import types
from unittest import mock

import pytest
from confluent_kafka import KafkaError, KafkaException

from benchmarks import kafka_admin

UNKNOWN_TOPIC_OR_PART: int = getattr(KafkaError, "UNKNOWN_TOPIC_OR_PART")
GROUP_ID_NOT_FOUND: int = getattr(KafkaError, "GROUP_ID_NOT_FOUND")
UNKNOWN_ERROR: int = getattr(KafkaError, "UNKNOWN")


def _future_with_exception(exc: Exception) -> mock.Mock:
    future = mock.Mock()
    future.result.side_effect = exc
    return future


def _future_with_result(value: object = None) -> mock.Mock:
    future = mock.Mock()
    future.result.return_value = value
    return future


def _make_kafka_exception(error_code: int) -> KafkaException:
    return KafkaException(KafkaError(error_code))


@pytest.fixture(autouse=True)
def freeze_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        kafka_admin, "time", types.SimpleNamespace(sleep=lambda _duration: None)
    )


def test_reset_topics_ignores_unknown_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = mock.Mock()
    unknown_error = _make_kafka_exception(UNKNOWN_TOPIC_OR_PART)
    admin.delete_topics.return_value = {"demo": _future_with_exception(unknown_error)}
    admin.delete_consumer_groups.return_value = {"baseline": _future_with_result()}
    admin.create_topics.return_value = {"demo": _future_with_result()}
    monkeypatch.setattr(kafka_admin, "AdminClient", mock.Mock(return_value=admin))

    kafka_admin.reset_topics_and_groups(
        bootstrap_servers="localhost:9092",
        topics={"demo": kafka_admin.TopicConfig(num_partitions=4)},
        consumer_groups=["baseline"],
        retries=1,
    )

    admin.delete_topics.assert_called_once()
    admin.create_topics.assert_called_once()


def test_reset_groups_ignores_missing_group(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = mock.Mock()
    admin.delete_topics.return_value = {"demo": _future_with_result()}
    missing_group_error = _make_kafka_exception(GROUP_ID_NOT_FOUND)
    admin.delete_consumer_groups.return_value = {
        "baseline": _future_with_exception(missing_group_error)
    }
    admin.create_topics.return_value = {"demo": _future_with_result()}
    monkeypatch.setattr(kafka_admin, "AdminClient", mock.Mock(return_value=admin))

    kafka_admin.reset_topics_and_groups(
        bootstrap_servers="localhost:9092",
        topics={"demo": kafka_admin.TopicConfig(num_partitions=4)},
        consumer_groups=["baseline"],
        retries=1,
    )

    admin.delete_consumer_groups.assert_called_once()


def test_reset_topics_raises_after_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = mock.Mock()
    failing_future = _future_with_exception(_make_kafka_exception(UNKNOWN_ERROR))
    admin.delete_topics.side_effect = [
        {"demo": failing_future},
        {"demo": failing_future},
    ]
    admin.delete_consumer_groups.return_value = {"baseline": _future_with_result()}
    admin.create_topics.return_value = {"demo": _future_with_result()}
    monkeypatch.setattr(kafka_admin, "AdminClient", mock.Mock(return_value=admin))

    with pytest.raises(KafkaException):
        kafka_admin.reset_topics_and_groups(
            bootstrap_servers="localhost:9092",
            topics={"demo": kafka_admin.TopicConfig(num_partitions=4)},
            consumer_groups=["baseline"],
            retries=2,
        )

    assert admin.delete_topics.call_count == 2
