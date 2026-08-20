# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/_work_manager_support.py
# Role: Provides shared fixtures and helper builders for split WorkManager unit tests.
# Extend here when WorkManager suites need common execution engine, partition, or poison-message fakes.

import asyncio
import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.poison_message import PoisonMessageCircuitBreaker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    OffsetRange,
    OrderingMode,
)
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.dto import WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine, BatchSubmitError


def _open_poison_circuit_for_key(
    circuit: PoisonMessageCircuitBreaker,
    tp: DtoTopicPartition,
    poison_key: bytes,
) -> None:
    failed_item = WorkItem(
        id=str(uuid.uuid4()),
        tp=tp,
        offset=999,
        epoch=1,
        key=b"route-key",
        payload=b"failed-payload",
        poison_key=poison_key,
    )
    circuit.record_completion(
        CompletionEvent(
            id=failed_item.id,
            tp=failed_item.tp,
            offset=failed_item.offset,
            epoch=failed_item.epoch,
            status=CompletionStatus.FAILURE,
            error="permanent failure",
            attempt=3,
        ),
        failed_item,
    )


__all__ = (
    "AsyncMock",
    "BaseExecutionEngine",
    "BatchSubmitError",
    "CompletionEvent",
    "CompletionStatus",
    "DtoTopicPartition",
    "MagicMock",
    "OffsetRange",
    "OffsetTracker",
    "OrderingMode",
    "PoisonMessageCircuitBreaker",
    "WorkItem",
    "WorkManager",
    "_open_poison_circuit_for_key",
    "asyncio",
    "patch",
    "pytest",
    "re",
    "uuid",
)
