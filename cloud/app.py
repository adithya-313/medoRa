"""FastAPI application factory with structured request logging."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from cloud.request_logging import RequestIdMiddleware


def create_app(service_name: str) -> FastAPI:
    """Create a FastAPI application configured with request ID middleware.

    Args:
        service_name: Service name for the application instance.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(title=service_name)
    app.add_middleware(RequestIdMiddleware)

    logger = logging.getLogger(f"cloud.{service_name}")

    @app.get("/health")
    async def health() -> dict[str, str]:
        logger.info("health check")
        return {"status": "ok"}

    return app
