"""Unit tests for structured JSON logging and request ID context."""

from __future__ import annotations

import datetime
import io
import json
import logging
import re

import pytest

from cloud.request_logging import (
    configure_logging,
    current_request_id,
    is_valid_request_id,
)

pytestmark = pytest.mark.unit


def test_all_keys_present_and_null_request_id() -> None:
    """Verify standard keys are present and request_id is null when unset."""
    stream = io.StringIO()
    configure_logging(service="test-service", stream=stream)

    logger = logging.getLogger("test.unit.keys")
    logger.info("standard log message")

    raw = stream.getvalue()
    lines = raw.strip().splitlines()
    assert len(lines) == 1

    data = json.loads(lines[0])
    assert set(data.keys()) == {
        "timestamp",
        "level",
        "service",
        "logger",
        "message",
        "request_id",
    }
    assert data["level"] == "INFO"
    assert data["service"] == "test-service"
    assert data["logger"] == "test.unit.keys"
    assert data["message"] == "standard log message"
    assert data["request_id"] is None


def test_request_id_present_when_context_set() -> None:
    """Verify request_id is extracted from ContextVar when populated."""
    stream = io.StringIO()
    configure_logging(service="test-service", stream=stream)

    logger = logging.getLogger("test.unit.reqid")
    token = current_request_id.set("trace-abc-123")
    try:
        logger.info("context-aware log")
    finally:
        current_request_id.reset(token)

    data = json.loads(stream.getvalue().strip())
    assert data["request_id"] == "trace-abc-123"


def test_newline_escaped_on_single_physical_line() -> None:
    """Verify message newlines are escaped and stay on one physical output line."""
    stream = io.StringIO()
    configure_logging(service="test-service", stream=stream)

    logger = logging.getLogger("test.unit.newlines")
    multiline_msg = "line one\nline two\nline three"
    logger.info(multiline_msg)

    raw = stream.getvalue()
    assert raw.count("\n") == 1

    data = json.loads(raw.strip())
    assert data["message"] == multiline_msg


def test_logger_exception_adds_exception_key() -> None:
    """Verify logger.exception adds 'exception' key with exception details."""
    stream = io.StringIO()
    configure_logging(service="test-service", stream=stream)

    logger = logging.getLogger("test.unit.exception")
    try:
        raise ValueError("simulated processing failure")
    except ValueError:
        logger.exception("handled operation error")

    raw = stream.getvalue()
    assert raw.count("\n") == 1

    data = json.loads(raw.strip())
    assert "exception" in data
    assert "ValueError" in data["exception"]
    assert "simulated processing failure" in data["exception"]


def test_configure_logging_twice_does_not_duplicate_lines() -> None:
    """Verify subsequent configure_logging calls replace existing handlers."""
    stream = io.StringIO()
    configure_logging(service="service-first", stream=stream)
    configure_logging(service="service-second", stream=stream)

    logger = logging.getLogger("test.unit.dedup")
    logger.info("must emit exactly one line")

    raw = stream.getvalue()
    lines = raw.strip().splitlines()
    assert len(lines) == 1

    data = json.loads(lines[0])
    assert data["service"] == "service-second"


def test_is_valid_request_id_rules() -> None:
    """Validate request ID character set and length constraints."""
    valid_samples = [
        "a",
        "Z",
        "9",
        "req-123",
        "trace_456.abc",
        "x" * 64,
    ]
    for sample in valid_samples:
        assert is_valid_request_id(sample) is True

    invalid_samples = [
        "",
        "x" * 65,
        "abc\n",
        "a b",
        "abc;rm",
        "bad#id",
        "@token",
    ]
    for sample in invalid_samples:
        assert is_valid_request_id(sample) is False


def test_timestamp_iso8601_utc() -> None:
    """Verify timestamp is valid UTC ISO-8601 with milliseconds and Z suffix."""
    stream = io.StringIO()
    configure_logging(service="test-service", stream=stream)

    logger = logging.getLogger("test.unit.timestamp")
    logger.info("timestamp probe")

    data = json.loads(stream.getvalue().strip())
    ts = data["timestamp"]
    assert ts.endswith("Z")
    assert re.search(r"\.\d{3}Z$", ts) is not None

    parsed = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.tzinfo == datetime.UTC
