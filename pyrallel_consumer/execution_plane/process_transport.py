from __future__ import annotations

import asyncio
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, NamedTuple

import msgpack  # type: ignore[import-untyped]

from pyrallel_consumer.dto import CompletionEvent, WorkItem

SerializedWorkItem = dict[str, Any]
InFlightRegistryKey = tuple[int, str, int, int]


@dataclass(frozen=True)
class RouteIdentity:
    topic: str
    partition: int
    key: Any


class LogicalWorkIdentity(NamedTuple):
    topic: str
    partition: int
    offset: int
    id: str
    epoch: int


class WorkerExecutionIdentity(NamedTuple):
    worker_index: int
    work: LogicalWorkIdentity


@dataclass(frozen=True)
class ProcessTransportCapabilities:
    pending_dispatch_recovery: bool = False


@dataclass(frozen=True)
class PendingDispatchRecovery:
    identity: WorkerExecutionIdentity
    payload: SerializedWorkItem


def resolve_route_identity(work_item: WorkItem) -> RouteIdentity:
    return RouteIdentity(
        topic=work_item.tp.topic,
        partition=work_item.tp.partition,
        key=work_item.key,
    )


def logical_work_identity_from_payload(
    payload: SerializedWorkItem,
) -> LogicalWorkIdentity:
    return LogicalWorkIdentity(
        topic=str(payload["topic"]),
        partition=int(payload["partition"]),
        offset=int(payload["offset"]),
        id=str(payload.get("id", "")),
        epoch=int(payload.get("epoch", 0)),
    )


def logical_work_identity_from_completion_event(
    event: CompletionEvent,
) -> LogicalWorkIdentity:
    return LogicalWorkIdentity(
        topic=event.tp.topic,
        partition=event.tp.partition,
        offset=event.offset,
        id=event.id,
        epoch=event.epoch,
    )


def logical_work_identity_from_registry_entry(
    key: InFlightRegistryKey,
    payload: SerializedWorkItem,
) -> LogicalWorkIdentity:
    _worker_index, topic, partition, offset = key
    return LogicalWorkIdentity(
        topic=topic,
        partition=partition,
        offset=offset,
        id=str(payload.get("id", "")),
        epoch=int(payload.get("epoch", 0)),
    )


def worker_execution_identity_from_payload(
    worker_index: int,
    payload: SerializedWorkItem,
) -> WorkerExecutionIdentity:
    return WorkerExecutionIdentity(
        worker_index=worker_index,
        work=logical_work_identity_from_payload(payload),
    )


def worker_execution_identity_from_registry_entry(
    key: InFlightRegistryKey,
    payload: SerializedWorkItem,
) -> WorkerExecutionIdentity:
    return WorkerExecutionIdentity(
        worker_index=key[0],
        work=logical_work_identity_from_registry_entry(key, payload),
    )


def registry_entry_matches_payload(
    key: InFlightRegistryKey,
    registry_payload: SerializedWorkItem,
    expected_payload: SerializedWorkItem,
) -> bool:
    return logical_work_identity_from_registry_entry(
        key, registry_payload
    ) == logical_work_identity_from_payload(expected_payload)


def stable_worker_index_for_route(
    route_identity: RouteIdentity,
    process_count: int,
) -> int:
    digest = hashlib.blake2b(
        msgpack.packb(
            {
                "topic": route_identity.topic,
                "partition": route_identity.partition,
                "key": route_identity.key,
            },
            use_bin_type=True,
        ),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big") % process_count


class ProcessTransport(ABC):
    @property
    def capabilities(self) -> ProcessTransportCapabilities:
        return ProcessTransportCapabilities()

    @abstractmethod
    async def submit_work_item(
        self,
        work_item: WorkItem,
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def dispatch_payload(
        self,
        payload: SerializedWorkItem,
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def start_worker_task_source(self, idx: int) -> tuple[Any, bool]:
        raise NotImplementedError

    @abstractmethod
    def handle_registry_event(self, event: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def recover_pending_dispatches(self, idx: int) -> list[PendingDispatchRecovery]:
        raise NotImplementedError

    @abstractmethod
    def requeue_payloads(self, payloads: list[SerializedWorkItem]) -> None:
        raise NotImplementedError

    @abstractmethod
    def clear_pending_dispatches(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def signal_shutdown(self, worker_count: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class AsyncToThreadSubmitMixin:
    async def submit_work_item(
        self,
        work_item: WorkItem,
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        serialize_work_item = getattr(self, "_serialize_work_item")
        dispatch_payload = getattr(self, "dispatch_payload")
        payload = serialize_work_item(work_item)
        await asyncio.to_thread(
            dispatch_payload,
            payload,
            route_identity=route_identity,
            count_in_flight=count_in_flight,
        )
