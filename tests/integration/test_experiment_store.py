"""Integration tests for Postgres experiment store and feature flags."""

from __future__ import annotations

import psycopg
import pytest

from cloud.experiments.store import (
    apply_schema,
    create_experiment,
    is_flag_enabled,
    log_exposure,
    set_flag,
)

pytestmark = pytest.mark.integration


def test_apply_schema_idempotence(postgres_url: str) -> None:
    """Verify apply_schema creates tables and runs multiple times without error.

    Purpose:
        Ensures DDL script is safe to execute repeatedly across deployments.

    Inputs/Outputs:
        postgres_url: Testcontainer connection URL.

    Key Logic:
        Applies schema twice; verifies existence of tables.

    Errors:
        psycopg.Error or AssertionError on failure.
    """
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        apply_schema(conn)
        apply_schema(conn)

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name IN ('feature_flags', 'experiments', 'exposures');
                """
            )
            tables = {row[0] for row in cur.fetchall()}
            assert tables == {"feature_flags", "experiments", "exposures"}


def test_feature_flag_global_and_override(postgres_url: str) -> None:
    """Verify global flags work and per-shop overrides take precedence.

    Purpose:
        Validates flag lookup hierarchy (shop override > global flag).

    Inputs/Outputs:
        postgres_url: Testcontainer connection URL.

    Key Logic:
        Sets global flag to False. Verifies global and shop lookups return False.
        Sets shop override to True for shop-1.
        Verifies shop-1 returns True, while shop-2 and global still return False.
        Updates global flag to True; verifies shop-2 now gets True while shop-1 override persists.

    Errors:
        AssertionError on unexpected flag state.
    """
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        apply_schema(conn)

        flag = "test_checkout_button"
        set_flag(conn, flag, enabled=False)

        assert is_flag_enabled(conn, flag) is False
        assert is_flag_enabled(conn, flag, shop_id="shop-1") is False
        assert is_flag_enabled(conn, flag, shop_id="shop-2") is False

        # Set override for shop-1
        set_flag(conn, flag, enabled=True, shop_id="shop-1")
        assert is_flag_enabled(conn, flag, shop_id="shop-1") is True
        assert is_flag_enabled(conn, flag, shop_id="shop-2") is False
        assert is_flag_enabled(conn, flag) is False

        # Update global flag to True
        set_flag(conn, flag, enabled=True)
        assert is_flag_enabled(conn, flag) is True
        assert is_flag_enabled(conn, flag, shop_id="shop-2") is True
        assert is_flag_enabled(conn, flag, shop_id="shop-1") is True


def test_feature_flag_missing_raises_value_error(postgres_url: str) -> None:
    """Verify querying an unconfigured flag raises ValueError without silent fallbacks.

    Purpose:
        Enforces code honesty by rejecting missing flag keys instead of returning False/None.

    Inputs/Outputs:
        postgres_url: Testcontainer connection URL.

    Key Logic:
        Queries a non-existent flag key; asserts ValueError is raised.

    Errors:
        AssertionError if ValueError is not raised.
    """
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        apply_schema(conn)
        with pytest.raises(ValueError, match="not found in store"):
            is_flag_enabled(conn, "completely_unknown_flag")
        with pytest.raises(ValueError, match="not found in store"):
            is_flag_enabled(conn, "completely_unknown_flag", shop_id="shop-1")


def test_create_experiment_and_validation(postgres_url: str) -> None:
    """Verify experiment registration and constraint validations.

    Purpose:
        Tests storage of valid experiment variants and error handling for invalid configs.

    Inputs/Outputs:
        postgres_url: Testcontainer connection URL.

    Key Logic:
        Registers a valid 3-way experiment.
        Attempts duplicate registration (asserts psycopg.Error).
        Attempts invalid variant weights (asserts ValueError).

    Errors:
        AssertionError if validation or uniqueness constraint fails.
    """
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        apply_schema(conn)

        key = "search-ranking-v1"
        variants = [("v1", 3333), ("v2", 3333), ("v3", 3334)]
        create_experiment(conn, key, variants)

        # Duplicate key fails with database constraint error
        with pytest.raises(psycopg.Error):
            create_experiment(conn, key, variants)

        # Invalid weights fail with ValueError
        with pytest.raises(ValueError, match="must sum to exactly 10000"):
            create_experiment(conn, "invalid-exp", [("A", 4000), ("B", 4000)])


def test_log_exposure_idempotence(postgres_url: str) -> None:
    """Verify log_exposure records once and returns False on subsequent duplicate calls.

    Purpose:
        Guarantees idempotent exposure logging per shop and experiment.

    Inputs/Outputs:
        postgres_url: Testcontainer connection URL.

    Key Logic:
        Logs first exposure; asserts return True.
        Logs second exposure with same key and shop; asserts return False.
        Queries database count to confirm only 1 row exists.

    Errors:
        AssertionError on non-idempotent behavior or incorrect counts.
    """
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        apply_schema(conn)

        exp_key = "hero-banner-v2"
        shop = "shop-987"
        variant = "B"

        first_logged = log_exposure(conn, exp_key, shop, variant)
        assert first_logged is True

        second_logged = log_exposure(conn, exp_key, shop, variant)
        assert second_logged is False

        # Attempting with a different variant for same shop still deduplicates
        third_logged = log_exposure(conn, exp_key, shop, "A")
        assert third_logged is False

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM exposures WHERE experiment_key = %s AND shop_id = %s;",
                (exp_key, shop),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 1
