from __future__ import annotations

import asyncio
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import msgpack  # type: ignore[import-untyped]

from pyrallel_consumer.dto import WorkItem

SerializedWorkItem = dict[str, Any]


@dataclass(frozen=True)
class RouteIdentity:
    topic: str
    partition: int
    key: Any


def resolve_route_identity(work_item: WorkItem) -> RouteIdentity:
    return RouteIdentity(
        topic=work_item.tp.topic,
        partition=work_item.tp.partition,
        key=work_item.key,
    )


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
    def recover_pending_dispatches(self, idx: int) -> list[SerializedWorkItem]:
        raise NotImplementedError

    @abstractmethod
    def requeue_payloads(self, payloads: list[SerializedWorkItem]) -> None:
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
