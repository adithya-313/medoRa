"""Structured JSON logging with request ID context."""

from __future__ import annotations

import datetime
import json
import logging
import re
import uuid
from contextvars import ContextVar
from typing import TextIO

REQUEST_ID_HEADER = "X-Request-ID"

current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)

_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")


def is_valid_request_id(value: str) -> bool:
    """Validate request ID format.

    Args:
        value: Candidate request ID string.

    Returns:
        True if value is 1-64 characters matching [A-Za-z0-9._-], False otherwise.
    """
    return bool(_REQUEST_ID_PATTERN.fullmatch(value))


def new_request_id() -> str:
    """Generate a new unique request ID as a uuid4 hex string.

    Returns:
        32-character hexadecimal UUID string.
    """
    return uuid.uuid4().hex


class JsonFormatter(logging.Formatter):
    """Formatter that outputs log records as single-line JSON objects."""

    def __init__(self, service: str) -> None:
        """Initialize formatter with a service identifier.

        Args:
            service: Service name included in all emitted log lines.
        """
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as a single JSON line.

        Args:
            record: Logging record to format.

        Returns:
            Single line of JSON with no raw newlines.
        """
        # Convert created timestamp to UTC datetime with millisecond precision
        created_dt = datetime.datetime.fromtimestamp(record.created, datetime.UTC)
        millis = int(created_dt.microsecond / 1000)
        timestamp_str = f"{created_dt.strftime('%Y-%m-%dT%H:%M:%S')}.{millis:03d}Z"

        log_data: dict[str, str | None] = {
            "timestamp": timestamp_str,
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": current_request_id.get(),
        }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False)


def configure_logging(service: str, level: int = logging.INFO, stream: TextIO | None = None) -> None:
    """Configure the root logger with a single JSON stream handler.

    Side effects:
        Removes all existing handlers on the root logger and sets a single
        StreamHandler using JsonFormatter, preventing duplicate output if called
        multiple times.

    Args:
        service: Service name to attach to all log records.
        level: Minimum logging level (default logging.INFO).
        stream: Optional stream destination (e.g. io.StringIO for testing, stdout by default).
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service=service))
    root_logger.addHandler(handler)
