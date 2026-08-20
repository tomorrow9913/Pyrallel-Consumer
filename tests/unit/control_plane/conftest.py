# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/conftest.py
# Role: Defines shared control-plane unit-test fixtures used by split test modules.
# Extend here when control-plane suites need package-local pytest fixtures.

from unittest.mock import AsyncMock, MagicMock

import pytest
from confluent_kafka import Consumer
from confluent_kafka import TopicPartition as KafkaTopicPartition
from confluent_kafka.admin import AdminClient

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


@pytest.fixture
def mock_kafka_config():
    config = MagicMock(spec=KafkaConfig)
    config.BOOTSTRAP_SERVERS = ["broker:9092"]
    config.get_consumer_config.return_value = {"group.id": "test_group"}
    config.get_producer_config.return_value = {}
    config.get_admin_config.return_value = {"bootstrap.servers": "broker:9092"}
    config.dlq_enabled = False

    execution = MagicMock()
    execution.max_retries = 3

    parallel_consumer = MagicMock()
    parallel_consumer.poll_batch_size = 1000
    parallel_consumer.worker_pool_size = 8
    parallel_consumer.execution = execution
    config.parallel_consumer = parallel_consumer

    return config


@pytest.fixture
def mock_execution_engine():
    return AsyncMock(spec=BaseExecutionEngine)


@pytest.fixture
def mock_consumer():
    consumer = MagicMock(spec=Consumer)
    consumer.assign.return_value = None
    consumer.unassign.return_value = None
    consumer.commit.return_value = None
    consumer.pause.return_value = None
    consumer.resume.return_value = None
    consumer.assignment.return_value = [
        KafkaTopicPartition("test-topic", 0),
        KafkaTopicPartition("test-topic", 1),
    ]
    return consumer


@pytest.fixture
def mock_admin_client():
    return MagicMock(spec=AdminClient)


@pytest.fixture
def mock_offset_tracker_factory():
    mock_factory = MagicMock()
    mock_factory.side_effect = (
        lambda tp, starting_offset, max_revoke_grace_ms, initial_completed_offsets: (
            AsyncMock(
                spec=OffsetTracker,
                topic_partition=tp,
                starting_offset=starting_offset,
                max_revoke_grace_ms=max_revoke_grace_ms,
            )
        )
    )
    return mock_factory


@pytest.fixture
def topic_partition():
    return DtoTopicPartition(topic="test-topic", partition=0)


@pytest.fixture
def completion_event(topic_partition):
    return CompletionEvent(
        id="work-1",
        tp=topic_partition,
        offset=0,
        epoch=1,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )


@pytest.fixture
def broker_poller(mock_kafka_config, mock_execution_engine):
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )
    poller.consumer = MagicMock(spec=Consumer)
    poller.producer = AsyncMock()
    poller.admin = AsyncMock()
    poller._cleanup = AsyncMock()
    poller._max_blocking_duration_ms = 0
    poller.MAX_IN_FLIGHT_MESSAGES = 1000
    poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 500
    poller.QUEUE_MAX_MESSAGES = 0
    return poller


@pytest.fixture
def work_manager(mock_execution_engine):
    return WorkManager(execution_engine=mock_execution_engine)


@pytest.fixture
def mock_dto_topic_partition():
    return DtoTopicPartition(topic="test-topic", partition=0)


@pytest.fixture
def mock_dto_topic_partition_1():
    return DtoTopicPartition(topic="test-topic", partition=1)


@pytest.fixture
def mock_offset_tracker_instance(mock_dto_topic_partition):
    tracker = MagicMock(
        spec=OffsetTracker(
            topic_partition=mock_dto_topic_partition,
            starting_offset=0,
            max_revoke_grace_ms=500,
        )
    )
    tracker.get_current_epoch.return_value = 1
    tracker.in_flight_count = 0
    tracker.get_gaps.return_value = []
    return tracker
