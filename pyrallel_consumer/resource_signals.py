from typing import Protocol

from pyrallel_consumer.dto import ResourceSignalSnapshot, ResourceSignalStatus


class ResourceSignalProvider(Protocol):
    """Supplies host resource snapshots without exposing sampling internals."""

    def snapshot(self) -> ResourceSignalSnapshot:
        """Return the current runtime snapshot."""
        ...


class NullResourceSignalProvider:
    """Fail-open provider used when resource sampling is unavailable or disabled."""

    def snapshot(self) -> ResourceSignalSnapshot:
        """Return the current runtime snapshot."""
        return ResourceSignalSnapshot(status=ResourceSignalStatus.UNAVAILABLE)
