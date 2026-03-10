import logging
import queue
from typing import Any, cast
from unittest.mock import Mock

import msgpack
import pytest

from pyrallel_consumer.config import ExecutionConfig, ProcessConfig
from pyrallel_consumer.dto import CompletionStatus, ExecutionMode, TopicPartition
from pyrallel_consumer.execution_plane.process_engine import (
    ProcessExecutionEngine,
    _completion_event_from_dict,
)


class _DeadWorker:
    exitcode = 1

    def is_alive(self) -> bool:
        return False


def test_ensure_workers_alive_does_not_requeue_timed_out_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(process_count=1),
    )
    engine_any._in_flight_registry = {
        (0, "topic", 1, 42): {
            "id": "work-42",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
            "requeue_attempts": 0,
            "timed_out": True,
        }
    }
    engine_any._task_queue = queue.Queue()
    engine_any._completion_queue = queue.Queue()
    engine_any._workers = [_DeadWorker()]
    engine_any._logger = logging.getLogger(__name__)

    replacement_worker = Mock()
    monkeypatch.setattr(engine, "_start_worker", lambda idx: replacement_worker)

    engine._ensure_workers_alive()

    assert engine_any._task_queue.empty()
    assert (0, "topic", 1, 42) not in engine_any._in_flight_registry
    assert engine_any._workers == [replacement_worker]

    raw_event = engine_any._completion_queue.get_nowait()
    event = _completion_event_from_dict(msgpack.unpackb(raw_event, raw=False))
    assert event.status == CompletionStatus.FAILURE
    assert event.error == "task_timeout"
    assert event.attempt == 1
    assert event.tp == TopicPartition("topic", 1)
    assert event.offset == 42
