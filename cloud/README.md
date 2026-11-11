# Cloud

FastAPI backend.
First built in Phase 1: Foundation, harness, flags and experiments.

## Structured Logging

Log records are formatted as single-line JSON objects via `JsonFormatter`. Each emitted line contains standard fields:

```json
{"timestamp": "2026-11-11T23:06:27.265Z", "level": "INFO", "service": "medora-cloud", "logger": "cloud.auth", "message": "user authenticated", "request_id": "req-7f8a9b0c"}
```

When an exception is logged via `logger.exception(...)`, an additional `exception` field contains the formatted traceback string. Newlines within messages and stack traces are escaped so each record occupies exactly one physical line.

## Request ID Conventions

The `RequestIdMiddleware` manages request context across asynchronous HTTP lifecycles:

- **Header Name**: `X-Request-ID`.
- **Accepted Characters**: 1–64 characters matching `[A-Za-z0-9._-]`.
- **Validation**: Incoming IDs that fail validation (invalid characters, length > 64, or empty) are replaced with a newly generated UUIDv4 hex string. The invalid input is never logged or echoed.
- **Echoed**: The resolved request ID is always returned in the HTTP response headers as `X-Request-ID`.
- **Forwarding**: Outbound HTTP requests propagate the active request ID using `outgoing_headers()`, ensuring end-to-end tracing across internal services without overwriting explicit caller overrides.
- **Lifecycle Cleanup**: The request ID context is stored in a Python `ContextVar` (`current_request_id`) and is strictly cleared (`current_request_id.reset(...)`) in a `finally` block after each request finishes.
