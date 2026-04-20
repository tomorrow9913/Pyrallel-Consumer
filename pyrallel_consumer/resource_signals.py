from typing import Protocol

from pyrallel_consumer.dto import ResourceSignalSnapshot, ResourceSignalStatus


class ResourceSignalProvider(Protocol):
    """Supplies host resource snapshots without exposing sampling internals."""

    def snapshot(self) -> ResourceSignalSnapshot:
        ...


class NullResourceSignalProvider:
    """Fail-open provider used when resource sampling is unavailable or disabled."""

    def snapshot(self) -> ResourceSignalSnapshot:
        return ResourceSignalSnapshot(status=ResourceSignalStatus.UNAVAILABLE)
