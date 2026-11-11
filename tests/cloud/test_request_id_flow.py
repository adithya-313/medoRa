"""Unit tests for request ID lifecycle, propagation, and middleware flow."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from cloud.app import create_app
from cloud.request_logging import (
    JsonFormatter,
    configure_logging,
    current_request_id,
    is_valid_request_id,
    outgoing_headers,
)

pytestmark = pytest.mark.unit


def test_no_incoming_header() -> None:
    """Verify new request ID is generated and attached to response and logs."""
    stream = io.StringIO()
    configure_logging(service="test-svc", stream=stream)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app = create_app("test-svc")

    async def run() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            header_id = response.headers.get("x-request-id")
            assert header_id is not None
            assert is_valid_request_id(header_id)

            raw_logs = stream.getvalue().strip().splitlines()
            assert len(raw_logs) == 1
            log_data = json.loads(raw_logs[0])
            assert log_data["message"] == "health check"
            assert log_data["request_id"] == header_id

    asyncio.run(run())


def test_valid_incoming_id_reused() -> None:
    """Verify valid incoming X-Request-ID is reused in response and logs."""
    stream = io.StringIO()
    configure_logging(service="test-svc", stream=stream)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app = create_app("test-svc")

    async def run() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health", headers={"X-Request-ID": "valid-trace-456"})
            assert response.status_code == 200
            assert response.headers.get("x-request-id") == "valid-trace-456"

            raw_logs = stream.getvalue().strip().splitlines()
            assert len(raw_logs) == 1
            log_data = json.loads(raw_logs[0])
            assert log_data["request_id"] == "valid-trace-456"

    asyncio.run(run())


def test_invalid_incoming_ids_replaced() -> None:
    """Verify invalid incoming IDs are replaced and never leaked into logs."""
    invalid_candidates = ["bad id", "x" * 65, "abc\ninjected"]
    for invalid_id in invalid_candidates:
        stream = io.StringIO()
        configure_logging(service="test-svc", stream=stream)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        app = create_app("test-svc")

        async def run(bad_id: str, current_app: FastAPI, current_stream: io.StringIO) -> None:
            transport = httpx.ASGITransport(app=current_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/health", headers={"X-Request-ID": bad_id})
                assert response.status_code == 200
                returned_id = response.headers.get("x-request-id")
                assert returned_id is not None
                assert is_valid_request_id(returned_id)
                assert returned_id != bad_id

                raw_logs = current_stream.getvalue()
                assert bad_id not in raw_logs
                lines = raw_logs.strip().splitlines()
                assert len(lines) == 1
                log_data = json.loads(lines[0])
                assert log_data["request_id"] == returned_id

        asyncio.run(run(invalid_id, app, stream))


def test_cross_service_propagation() -> None:
    """Verify request ID propagates from service A to service B with distinct service names."""
    stream = io.StringIO()
    logging.getLogger("httpx").setLevel(logging.WARNING)

    svc_b = create_app("svc-b")
    svc_a = create_app("svc-a")

    logger_a = logging.getLogger("cloud.svc-a")
    handler_a = logging.StreamHandler(stream)
    handler_a.setFormatter(JsonFormatter(service="svc-a"))
    logger_a.addHandler(handler_a)
    logger_a.propagate = False

    logger_b = logging.getLogger("cloud.svc-b")
    handler_b = logging.StreamHandler(stream)
    handler_b.setFormatter(JsonFormatter(service="svc-b"))
    logger_b.addHandler(handler_b)
    logger_b.propagate = False

    b_transport = httpx.ASGITransport(app=svc_b)

    @svc_a.get("/call-b")
    async def call_b() -> dict[str, Any]:
        logger_a.info("service a calling service b")
        async with httpx.AsyncClient(transport=b_transport, base_url="http://svc-b") as client:
            resp = await client.get("/health", headers=outgoing_headers())
            return {"b_status": resp.status_code, "b_data": resp.json()}

    async def run() -> None:
        a_transport = httpx.ASGITransport(app=svc_a)
        async with httpx.AsyncClient(transport=a_transport, base_url="http://svc-a") as client:
            response = await client.get("/call-b", headers={"X-Request-ID": "trace-123"})
            assert response.status_code == 200
            assert response.headers.get("x-request-id") == "trace-123"

            lines = stream.getvalue().strip().splitlines()
            assert len(lines) == 2
            records = [json.loads(line) for line in lines]
            a_records = [r for r in records if r["service"] == "svc-a"]
            b_records = [r for r in records if r["service"] == "svc-b"]

            assert len(a_records) == 1
            assert len(b_records) == 1
            assert a_records[0]["request_id"] == "trace-123"
            assert b_records[0]["request_id"] == "trace-123"
            assert a_records[0]["service"] != b_records[0]["service"]

    try:
        asyncio.run(run())
    finally:
        logger_a.removeHandler(handler_a)
        logger_a.propagate = True
        logger_b.removeHandler(handler_b)
        logger_b.propagate = True


def test_concurrency_no_mixups() -> None:
    """Verify concurrent requests retain isolated request IDs in responses and logs."""
    stream = io.StringIO()
    configure_logging(service="concurrent-svc", stream=stream)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app = create_app("concurrent-svc")

    async def run() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

            async def send_req(i: int) -> tuple[str, str]:
                req_id = f"concurrent-trace-{i:02d}"
                resp = await client.get("/health", headers={"X-Request-ID": req_id})
                assert resp.status_code == 200
                header_val = resp.headers.get("x-request-id")
                assert header_val is not None
                return req_id, header_val

            tasks = [send_req(i) for i in range(20)]
            results = await asyncio.gather(*tasks)

            for sent_id, returned_id in results:
                assert sent_id == returned_id

            raw_lines = stream.getvalue().strip().splitlines()
            assert len(raw_lines) == 20
            log_req_ids = [json.loads(line)["request_id"] for line in raw_lines]
            expected_ids = [f"concurrent-trace-{i:02d}" for i in range(20)]
            assert sorted(log_req_ids) == sorted(expected_ids)

    asyncio.run(run())


def test_context_reset_after_request_finishes() -> None:
    """Verify ContextVar is None after request completion."""
    app = create_app("test-svc")

    async def run() -> None:
        assert current_request_id.get() is None
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health", headers={"X-Request-ID": "temp-trace-999"})
            assert response.status_code == 200
        assert current_request_id.get() is None

    asyncio.run(run())
    assert current_request_id.get() is None


def test_outgoing_headers() -> None:
    """Verify outgoing_headers behavior when set, unset, or overridden."""
    assert current_request_id.get() is None
    headers_unset = outgoing_headers({"Authorization": "Bearer token"})
    assert "X-Request-ID" not in headers_unset
    assert headers_unset["Authorization"] == "Bearer token"

    token = current_request_id.set("propagated-trace-777")
    try:
        headers_set = outgoing_headers({"Authorization": "Bearer token"})
        assert headers_set["X-Request-ID"] == "propagated-trace-777"
        assert headers_set["Authorization"] == "Bearer token"

        headers_override = outgoing_headers(
            {
                "X-Request-ID": "caller-override-888",
                "Accept": "application/json",
            }
        )
        assert headers_override["X-Request-ID"] == "caller-override-888"
        assert headers_override["Accept"] == "application/json"
    finally:
        current_request_id.reset(token)
