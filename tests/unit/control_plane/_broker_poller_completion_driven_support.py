# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/_broker_poller_completion_driven_support.py
# Role: Provides shared fixtures and helpers for completion-driven BrokerPoller tests.
# Extend here when completion-driven poller suites need common completion, tracker, or consumer-loop fakes.

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from confluent_kafka import Consumer
from confluent_kafka import TopicPartition as KafkaTopicPartition

from pyrallel_consumer.config import CommitCoordinatorConfig, KafkaConfig
from pyrallel_consumer.control_plane.broker_completion_support import (
    CompletionProcessingResult,
)
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.commit_coordinator import (
    CommitBatchAborted,
    CommitCandidate,
    CommitCoordinator,
    CommitSettlement,
)
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


def _make_tracker(tp):
    tracker = MagicMock(spec=OffsetTracker)
    tracker.topic_partition = tp
    tracker.last_committed_offset = -1
    tracker.last_fetched_offset = -1
    tracker.completed_offsets = set()
    tracker.get_current_epoch.return_value = 1
    return tracker


def _run_consume_loop_once_then_stop(broker_poller, first_batch):
    call_count = 0

    def fake_consume(num_messages=1, timeout=0.1):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return first_batch
        broker_poller._running = False
        return []

    broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)


__all__ = (
    "AsyncMock",
    "BaseExecutionEngine",
    "BrokerPoller",
    "CommitBatchAborted",
    "CommitCandidate",
    "CommitCoordinator",
    "CommitCoordinatorConfig",
    "CommitSettlement",
    "CompletionEvent",
    "CompletionProcessingResult",
    "CompletionStatus",
    "Consumer",
    "DtoTopicPartition",
    "KafkaConfig",
    "KafkaTopicPartition",
    "MagicMock",
    "OffsetTracker",
    "_make_tracker",
    "_run_consume_loop_once_then_stop",
    "asyncio",
    "call",
    "patch",
    "pytest",
    "threading",
    "time",
)
