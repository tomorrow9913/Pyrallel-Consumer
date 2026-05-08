# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/_broker_poller_support.py
# Role: Provides shared fixtures and helper builders for split BrokerPoller unit tests.
# Extend here when BrokerPoller tests need common Kafka, consumer, or tracker fakes.

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from confluent_kafka import Consumer, KafkaException
from confluent_kafka import TopicPartition as KafkaTopicPartition
from confluent_kafka.admin import AdminClient

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import (
    BrokerPoller,
    RevokePreparation,
)
from pyrallel_consumer.control_plane.broker_rebalance_bridge import (
    BrokerRebalanceBridge,
)
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    ExecutionMode,
    OrderingMode,
)
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine  # Added import


def _make_message(
    topic: str,
    partition: int,
    offset: int,
    key: bytes,
    value: bytes,
):
    message = MagicMock()
    message.topic.return_value = topic
    message.partition.return_value = partition
    message.offset.return_value = offset
    message.key.return_value = key
    message.value.return_value = value
    message.error.return_value = None
    return message


__all__ = (
    "AdminClient",
    "AsyncMock",
    "BaseExecutionEngine",
    "BrokerPoller",
    "BrokerRebalanceBridge",
    "CompletionEvent",
    "CompletionStatus",
    "Consumer",
    "DtoTopicPartition",
    "ExecutionMode",
    "KafkaConfig",
    "KafkaException",
    "KafkaTopicPartition",
    "MagicMock",
    "OffsetTracker",
    "OrderingMode",
    "RevokePreparation",
    "_make_message",
    "asyncio",
    "patch",
    "pytest",
    "threading",
)
