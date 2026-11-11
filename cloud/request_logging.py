"""Structured JSON logging with request ID context."""

from __future__ import annotations

import datetime
import json
import logging
import re
import uuid
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any, TextIO

from starlette.types import ASGIApp, Receive, Scope, Send

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


class RequestIdMiddleware:
    """Pure ASGI middleware managing X-Request-ID in context and headers."""

    def __init__(self, app: ASGIApp) -> None:
        """Initialize middleware wrapping an ASGI application.

        Args:
            app: The ASGI application to wrap.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process ASGI scope.

        Extracts or generates X-Request-ID for HTTP scopes, stores it in
        the current_request_id ContextVar, injects it into response headers,
        and ensures context cleanup upon completion.

        Args:
            scope: ASGI scope dictionary.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        incoming_id: str | None = None
        for name, value in raw_headers:
            if name.lower() == b"x-request-id":
                # latin-1 maps every byte to a character, so decoding cannot fail; validation rejects anything unsafe.
                candidate = value.decode("latin1")
                if is_valid_request_id(candidate):
                    incoming_id = candidate
                break

        request_id = incoming_id if incoming_id is not None else new_request_id()

        async def send_with_request_id(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin1")))
                message["headers"] = headers
            await send(message)

        token = current_request_id.set(request_id)
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            current_request_id.reset(token)


def outgoing_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Prepare outbound HTTP headers with propagated request ID.

    Includes X-Request-ID from current_request_id if active.
    Never overwrites an explicit X-Request-ID passed by the caller in extra.

    Args:
        extra: Optional dictionary of existing headers to send.

    Returns:
        Header dictionary containing extra items and propagated X-Request-ID.
    """
    headers: dict[str, str] = dict(extra) if extra is not None else {}

    has_explicit_id = any(k.lower() == REQUEST_ID_HEADER.lower() for k in headers)
    if not has_explicit_id:
        active_id = current_request_id.get()
        if active_id is not None:
            headers[REQUEST_ID_HEADER] = active_id

    return headers
