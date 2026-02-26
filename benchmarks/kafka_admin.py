from __future__ import annotations

import logging
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping

from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic

logger = logging.getLogger(__name__)

UNKNOWN_TOPIC_CODE: int = getattr(KafkaError, "UNKNOWN_TOPIC_OR_PART")
GROUP_ID_NOT_FOUND_CODE: int = getattr(KafkaError, "GROUP_ID_NOT_FOUND")
UNKNOWN_ERROR_CODE: int = getattr(KafkaError, "UNKNOWN")


@dataclass(frozen=True)
class TopicConfig:
    num_partitions: int
    replication_factor: int = 1
    configs: Mapping[str, str] | None = None


def reset_topics_and_groups(
    *,
    bootstrap_servers: str,
    topics: Dict[str, TopicConfig],
    consumer_groups: Iterable[str],
    retries: int = 3,
    backoff_sec: float = 0.5,
    operation_timeout: float = 10.0,
) -> None:
    if not topics:
        raise ValueError("At least one topic must be provided for reset")

    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    _delete_topics_with_retries(
        admin,
        list(topics.keys()),
        retries=retries,
        backoff_sec=backoff_sec,
        operation_timeout=operation_timeout,
    )
    _delete_groups_with_retries(
        admin,
        list(consumer_groups),
        retries=retries,
        backoff_sec=backoff_sec,
    )
    _create_topics_with_retries(
        admin,
        topics,
        retries=retries,
        backoff_sec=backoff_sec,
        operation_timeout=operation_timeout,
    )


def _delete_topics_with_retries(
    admin: AdminClient,
    topics: list[str],
    *,
    retries: int,
    backoff_sec: float,
    operation_timeout: float,
) -> None:
    if not topics:
        return
    for attempt in range(1, retries + 1):
        futures = admin.delete_topics(topics, operation_timeout=operation_timeout)
        try:
            _await_admin_results(
                futures,
                ignored_codes={UNKNOWN_TOPIC_CODE},
                action="delete topics",
            )
            logger.debug("Deleted topics: %s", ", ".join(topics))
            return
        except KafkaException as exc:
            if attempt == retries:
                raise
            logger.warning(
                "Topic deletion failed (attempt %d/%d): %s",
                attempt,
                retries,
                exc,
            )
            time.sleep(backoff_sec * attempt)


def _delete_groups_with_retries(
    admin: AdminClient,
    groups: list[str],
    *,
    retries: int,
    backoff_sec: float,
) -> None:
    if not groups:
        return
    for attempt in range(1, retries + 1):
        futures = admin.delete_consumer_groups(groups)
        try:
            _await_admin_results(
                futures,
                ignored_codes={GROUP_ID_NOT_FOUND_CODE},
                action="delete consumer groups",
            )
            logger.debug("Deleted consumer groups: %s", ", ".join(groups))
            return
        except KafkaException as exc:
            if attempt == retries:
                raise
            logger.warning(
                "Consumer group deletion failed (attempt %d/%d): %s",
                attempt,
                retries,
                exc,
            )
            time.sleep(backoff_sec * attempt)


def _create_topics_with_retries(
    admin: AdminClient,
    topics: Dict[str, TopicConfig],
    *,
    retries: int,
    backoff_sec: float,
    operation_timeout: float,
) -> None:
    new_topics = [
        NewTopic(
            topic=name,
            num_partitions=config.num_partitions,
            replication_factor=config.replication_factor,
            config=dict(config.configs or {}),
        )
        for name, config in topics.items()
    ]
    for attempt in range(1, retries + 1):
        futures = admin.create_topics(new_topics, operation_timeout=operation_timeout)
        try:
            _await_admin_results(futures, ignored_codes=set(), action="create topics")
            logger.debug("Created topics: %s", ", ".join(topics.keys()))
            return
        except KafkaException as exc:
            if attempt == retries:
                raise
            logger.warning(
                "Topic creation failed (attempt %d/%d): %s",
                attempt,
                retries,
                exc,
            )
            time.sleep(backoff_sec * attempt)


def _await_admin_results(
    futures: Mapping[str, Future[None]],
    *,
    ignored_codes: set[int],
    action: str,
) -> None:
    for name, future in futures.items():
        try:
            future.result()
        except KafkaException as exc:
            if _should_ignore(exc, ignored_codes):
                logger.debug("Ignoring %s error for %s: %s", action, name, exc)
                continue
            raise


def _should_ignore(exc: KafkaException, ignored_codes: set[int]) -> bool:
    if not ignored_codes:
        return False
    err = exc.args[0] if exc.args else None
    if isinstance(err, KafkaError) and err.code() in ignored_codes:
        return True
    return False
