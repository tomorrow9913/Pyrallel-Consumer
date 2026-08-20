from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import TypeVar

_OperationResult = TypeVar("_OperationResult")


class BrokerOperationGuard:
    """Serialize blocking broker-client operations across thread boundaries."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def run(self, operation: Callable[[], _OperationResult]) -> _OperationResult:
        """Run a broker operation while holding the reentrant guard."""
        with self._lock:
            return operation()

    async def run_off_event_loop(
        self, operation: Callable[[], _OperationResult]
    ) -> _OperationResult:
        """Run a guarded broker operation without blocking the event loop."""
        return await asyncio.to_thread(self.run, operation)
