# Placeholder for LogManager
import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


class LogManager:
    @staticmethod
    def get_logger(name):
        return logging.getLogger(name)
