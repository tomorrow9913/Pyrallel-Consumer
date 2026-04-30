# Placeholder for LogManager
import logging
import logging.handlers
from multiprocessing import Queue
from typing import Tuple

# Library best practice: add NullHandler so log messages are silently
# discarded unless the application configures logging.
logging.getLogger(__name__).addHandler(logging.NullHandler())


class LogManager:
    """Create loggers and queue listeners for worker processes."""

    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        """Return logger for process-safe logging."""
        return logging.getLogger(name)

    @staticmethod
    def setup_worker_logging(log_queue: "Queue[logging.LogRecord]") -> None:
        """Handle setup worker logging within process-safe logging."""
        root = logging.getLogger()
        root.handlers.clear()
        handler = logging.handlers.QueueHandler(log_queue)
        root.addHandler(handler)
        root.setLevel(logging.INFO)

    @staticmethod
    def create_queue_listener(
        log_queue: "Queue[logging.LogRecord]",
        handlers: Tuple[logging.Handler, ...] = (),
    ) -> logging.handlers.QueueListener:
        """Create queue listener for process-safe logging."""
        if not handlers:
            handlers = tuple(logging.getLogger().handlers)
        return logging.handlers.QueueListener(
            log_queue, *handlers, respect_handler_level=True
        )
