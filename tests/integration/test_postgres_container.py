"""Integration tests verifying real PostgreSQL operation via Testcontainers."""

import psycopg
import pytest

pytestmark = pytest.mark.integration


def test_postgres_version_reports_seventeen(postgres_url: str) -> None:
    """Verify that the PostgreSQL container reports version 17.

    Purpose:
        Validates container image version and database connectivity.

    Inputs/Outputs:
        postgres_url: Connection URL string provided by postgres_url fixture.

    Key Logic:
        Connects via psycopg.connect and executes SELECT version().
        Asserts that 'PostgreSQL 17' is in the returned version string.

    Errors:
        AssertionError if 'PostgreSQL 17' not present in version query result.
    """
    with psycopg.connect(postgres_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            row = cur.fetchone()
            assert row is not None
            assert "PostgreSQL 17" in row[0]


def test_not_null_constraint_violation_raises_error(postgres_url: str) -> None:
    """Verify that inserting a NULL into a NOT NULL column raises NotNullViolation.

    Purpose:
        Ensures PostgreSQL schema integrity constraints are enforced.

    Inputs/Outputs:
        postgres_url: Connection URL string provided by postgres_url fixture.

    Key Logic:
        Creates a table test_not_null_t1 with a NOT NULL text column.
        Attempts to insert NULL into the required column.
        Asserts that psycopg.errors.NotNullViolation is raised.

    Errors:
        AssertionError if NotNullViolation is not raised.
    """
    table_name = "test_not_null_t1"
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"CREATE TABLE {table_name} (id serial PRIMARY KEY, name text NOT NULL)")
            with pytest.raises(psycopg.errors.NotNullViolation):
                cur.execute(f"INSERT INTO {table_name} (name) VALUES (NULL)")


def test_transaction_rollback_discards_inserted_rows(postgres_url: str) -> None:
    """Verify that rolling back a transaction discards all inserted rows.

    Purpose:
        Demonstrates transactional atomicity and rollback mechanics.

    Inputs/Outputs:
        postgres_url: Connection URL string provided by postgres_url fixture.

    Key Logic:
        Creates a table test_tx_rollback_t2.
        Begins a transaction, inserts three rows, and issues an explicit rollback.
        Queries row count and verifies it is exactly zero.

    Errors:
        AssertionError if row count is not 0 after rollback.
    """
    table_name = "test_tx_rollback_t2"
    with psycopg.connect(postgres_url) as conn:
        with conn.cursor() as cur:
            cur.execute(f"CREATE TABLE {table_name} (id serial PRIMARY KEY, item_val int)")
        conn.commit()

        # In psycopg v3, connection is in a transaction block by default
        with conn.cursor() as cur:
            for val in [10, 20, 30]:
                cur.execute(f"INSERT INTO {table_name} (item_val) VALUES (%s)", (val,))
        conn.rollback()

        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table_name}")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 0
