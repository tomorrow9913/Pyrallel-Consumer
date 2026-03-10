from __future__ import annotations

import asyncio
import sys
from asyncio.subprocess import PIPE
from collections.abc import Callable

from benchmarks.tui.log_parser import BenchmarkLogParser, BenchmarkProgressSnapshot
from benchmarks.tui.state import BenchmarkTuiState


class BenchmarkProcessController:
    def __init__(
        self,
        *,
        state: BenchmarkTuiState,
        on_output: Callable[[str, bool], None],
        on_progress: Callable[[BenchmarkProgressSnapshot], None],
        on_complete: Callable[[int], None],
    ) -> None:
        self._state = state
        self._on_output = on_output
        self._on_progress = on_progress
        self._on_complete = on_complete
        self._parser = BenchmarkLogParser(workload_mode=state.workload)
        self._process: asyncio.subprocess.Process | None = None
        self._cancel_requested = False

    async def run(self) -> None:
        argv = [
            sys.executable,
            "-m",
            "benchmarks.run_parallel_benchmark",
            *self._state.to_argv(),
        ]
        self._process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=PIPE,
            stderr=PIPE,
        )
        if self._cancel_requested:
            await self.cancel()
        await asyncio.gather(
            self._read_stream(self._process.stdout, is_error=False),
            self._read_stream(self._process.stderr, is_error=True),
        )
        return_code = await self._process.wait()
        self._on_complete(return_code)

    async def cancel(self) -> None:
        self._cancel_requested = True
        if self._process is None or self._process.returncode is not None:
            return
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=2.0)
        except TimeoutError:
            self._process.kill()
            await self._process.wait()

    async def _read_stream(
        self, stream: asyncio.StreamReader | None, *, is_error: bool
    ) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            decoded = line.decode("utf-8", errors="replace").rstrip()
            if not decoded:
                continue
            self._on_output(decoded, is_error)
            snapshot = self._parser.consume(decoded)
            self._on_progress(snapshot)
