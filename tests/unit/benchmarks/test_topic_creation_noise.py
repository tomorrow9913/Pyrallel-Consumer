from __future__ import annotations

from unittest import mock

from benchmarks import producer, pyrallel_consumer_test


class _Metadata:
    def __init__(self, topics: dict[str, object]) -> None:
        self.topics = topics


def test_producer_create_topic_if_not_exists_skips_existing_topic(monkeypatch) -> None:
    # Given: inputs for `producer create topic if not exists skips exi...` are prepared.
    admin = mock.Mock()
    admin.list_topics.return_value = _Metadata({"demo": object()})
    monkeypatch.setattr(producer, "AdminClient", mock.Mock(return_value=admin))
    print_mock = mock.Mock()
    monkeypatch.setattr("builtins.print", print_mock)

    # When: the topic creation helper code path is exercised.
    producer.create_topic_if_not_exists(
        {"bootstrap.servers": "localhost:9092"},
        "demo",
        num_partitions=3,
    )

    # Then: the expected `producer create topic if not exists skips exi...` behavior is asserted.
    admin.create_topics.assert_not_called()
    print_mock.assert_not_called()


def test_pyrallel_create_topic_if_not_exists_skips_existing_topic(monkeypatch) -> None:
    # Given: inputs for `pyrallel create topic if not exists skips exi...` are prepared.
    admin = mock.Mock()
    admin.list_topics.return_value = _Metadata({"demo": object()})
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "AdminClient",
        mock.Mock(return_value=admin),
    )
    print_mock = mock.Mock()
    monkeypatch.setattr("builtins.print", print_mock)

    # When: the topic creation helper code path is exercised.
    pyrallel_consumer_test.create_topic_if_not_exists(
        {"bootstrap.servers": "localhost:9092"},
        "demo",
        num_partitions=3,
    )

    # Then: the expected `pyrallel create topic if not exists skips exi...` behavior is asserted.
    admin.create_topics.assert_not_called()
    print_mock.assert_not_called()
