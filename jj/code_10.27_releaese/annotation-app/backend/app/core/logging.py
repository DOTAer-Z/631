import logging
from typing import Any

from app.core.config import get_settings

class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        message = record.getMessage()
        logger_name = record.name
        level = record.levelname
        return f"time={timestamp} level={level} logger={logger_name} message={message}"


def init_logging(level: int = logging.INFO) -> None:
    settings = get_settings()
    resolved_level = getattr(logging, settings.log_level.upper(), level)

    handler = logging.StreamHandler()
    formatter = KeyValueFormatter(datefmt="%Y-%m-%dT%H:%M:%S%z")
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(resolved_level)

    root_logger.handlers.clear()
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
