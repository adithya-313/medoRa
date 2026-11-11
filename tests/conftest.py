"""Root test configuration and plugin declarations."""

from collections.abc import Generator

import pytest
from testcontainers.community.postgres import PostgresContainer

pytest_plugins = ["pytester", "tests.support.tier_markers"]

# POSTGRES_IMAGE: test-only choice, the production version is decided in Phase 2.
POSTGRES_IMAGE = "postgres:17-alpine"


@pytest.fixture(scope="session")
def postgres_url() -> Generator[str, None, None]:
    """Start a PostgreSQL container for integration testing.

    Purpose:
        Provides a session-scoped connection URL to a real PostgreSQL instance.

    Inputs/Outputs:
        Yields a PostgreSQL connection URL string accepted by psycopg.

    Key Logic:
        Instantiates PostgresContainer with POSTGRES_IMAGE and driver=None.
        Attempts to start the container; raises RuntimeError if Docker is unreachable.
        Yields connection URL.
        Stops the container in a finally block to ensure cleanup.

    Errors:
        RuntimeError: Raised if Docker cannot be reached, wrapping original error.
    """
    container = None
    try:
        try:
            container = PostgresContainer(POSTGRES_IMAGE, driver=None)
            container.start()
        except Exception as err:
            raise RuntimeError("integration tests need Docker running") from err
        yield container.get_connection_url()
    finally:
        if container is not None:
            container.stop()
