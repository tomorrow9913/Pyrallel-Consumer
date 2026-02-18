# Placeholder for LogManager
import logging
import logging.handlers
from multiprocessing import Queue
from typing import Tuple

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


class LogManager:
    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)

    @staticmethod
    def setup_worker_logging(log_queue: "Queue[logging.LogRecord]") -> None:
        root = logging.getLogger()
        root.handlers.clear()
        handler = logging.handlers.QueueHandler(log_queue)
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)

    @staticmethod
    def create_queue_listener(
        log_queue: "Queue[logging.LogRecord]",
        handlers: Tuple[logging.Handler, ...] = (),
    ) -> logging.handlers.QueueListener:
        if not handlers:
            handlers = tuple(logging.getLogger().handlers)
        return logging.handlers.QueueListener(
            log_queue, *handlers, respect_handler_level=True
        )
