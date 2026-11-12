"""Integration tests for Postgres experiment store, lifecycle states, and feature flags."""

from __future__ import annotations

import psycopg
import pytest

from cloud.experiments.assignment import assign_variant
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


def test_unknown_flag_returns_false(postgres_url: str) -> None:
    """Verify querying an unconfigured flag returns False per specification.

    Purpose:
        Enforces specification rule that unknown flags evaluate to disabled (False).

    Inputs/Outputs:
        postgres_url: Testcontainer connection URL.

    Key Logic:
        Queries a non-existent flag key with and without shop_id; asserts both return False.

    Errors:
        AssertionError if unknown flag does not return False.
    """
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        apply_schema(conn)
        assert is_flag_enabled(conn, "completely_unknown_flag") is False
        assert is_flag_enabled(conn, "completely_unknown_flag", shop_id="shop-1") is False


def test_create_experiment_and_validation(postgres_url: str) -> None:
    """Verify experiment registration, lifecycle status check, and constraints.

    Purpose:
        Tests storage of experiment variants across valid statuses ('draft', 'running', 'stopped')
        and error handling for invalid statuses and configurations.

    Inputs/Outputs:
        postgres_url: Testcontainer connection URL.

    Key Logic:
        Registers experiments under valid status values.
        Attempts registration with invalid status (asserts ValueError).
        Attempts duplicate registration (asserts psycopg.Error).
        Attempts invalid variant weights (asserts ValueError).

    Errors:
        AssertionError if validation or uniqueness constraint fails.
    """
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        apply_schema(conn)

        key = "search-ranking-v1"
        variants = [("v1", 3333), ("v2", 3333), ("v3", 3334)]
        create_experiment(conn, key, variants, status="running")

        # Other valid lifecycle statuses
        create_experiment(conn, "exp-draft", [("A", 10000)], status="draft")
        create_experiment(conn, "exp-stopped", [("A", 10000)], status="stopped")

        # Invalid status fails with ValueError
        with pytest.raises(ValueError, match="status must be one of"):
            create_experiment(conn, "exp-invalid-status", [("A", 10000)], status="archived")

        # Duplicate key fails with database constraint error
        with pytest.raises(psycopg.Error):
            create_experiment(conn, key, variants, status="running")

        # Invalid weights fail with ValueError
        with pytest.raises(ValueError, match="must sum to exactly 10000"):
            create_experiment(conn, "invalid-exp", [("A", 4000), ("B", 4000)], status="running")


def test_stopped_or_draft_experiment_raises(postgres_url: str) -> None:
    """Verify log_exposure raises ValueError when experiment is not running.

    Purpose:
        Ensures traffic exposure is blocked for draft or stopped experiments.

    Inputs/Outputs:
        postgres_url: Testcontainer connection URL.

    Key Logic:
        Registers draft and stopped experiments; asserts log_exposure raises ValueError.

    Errors:
        AssertionError if ValueError is not raised for non-running experiments.
    """
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        apply_schema(conn)
        create_experiment(conn, "exp-draft-gate", [("A", 5000), ("B", 5000)], status="draft")
        create_experiment(conn, "exp-stopped-gate", [("A", 5000), ("B", 5000)], status="stopped")

        with pytest.raises(ValueError, match="is not running"):
            log_exposure(conn, "exp-draft-gate", "shop-00001")

        with pytest.raises(ValueError, match="is not running"):
            log_exposure(conn, "exp-stopped-gate", "shop-00001")


def test_unknown_experiment_raises(postgres_url: str) -> None:
    """Verify log_exposure raises ValueError when the experiment does not exist.

    Purpose:
        Ensures exposure logging fails clearly when referencing a non-existent experiment.

    Inputs/Outputs:
        postgres_url: Testcontainer connection URL.

    Key Logic:
        Attempts to log exposure for a non-existent experiment key.

    Errors:
        AssertionError if ValueError is not raised.
    """
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        apply_schema(conn)
        with pytest.raises(ValueError, match="does not exist"):
            log_exposure(conn, "nonexistent-exp", "shop-00001")


def test_stored_variant_matches_assign_variant(postgres_url: str) -> None:
    """Verify log_exposure computes variant with assign_variant and stores it idempotently.

    Purpose:
        Guarantees that stored variant in the database matches assign_variant deterministically
        and repeated calls return the same stored variant without creating duplicate rows.

    Inputs/Outputs:
        postgres_url: Testcontainer connection URL.

    Key Logic:
        Registers running experiment.
        Iterates over 50 shops; asserts returned variant matches assign_variant.
        Calls log_exposure a second time; asserts identical return value.
        Asserts total exposures count in DB equals exactly 50.

    Errors:
        AssertionError on variant mismatch or non-idempotent behavior.
    """
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        apply_schema(conn)

        exp_key = "hero-banner-v2"
        variants = [("control", 5000), ("treatment", 5000)]
        create_experiment(conn, exp_key, variants, status="running")

        for i in range(1, 51):
            shop = f"shop-{i:05d}"
            expected = assign_variant(exp_key, shop, variants)
            first_stored = log_exposure(conn, exp_key, shop)
            assert first_stored == expected

            second_stored = log_exposure(conn, exp_key, shop)
            assert second_stored == expected

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM exposures WHERE experiment_key = %s;", (exp_key,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 50


def test_foreign_key_rejects_direct_insert_for_unknown_experiment(postgres_url: str) -> None:
    """Verify database foreign key constraint rejects direct inserts referencing unknown experiment.

    Purpose:
        Validates referential integrity from exposures.experiment_key to experiments(experiment_key).

    Inputs/Outputs:
        postgres_url: Testcontainer connection URL.

    Key Logic:
        Executes a raw SQL insert targeting an unmapped experiment_key.
        Asserts psycopg.errors.ForeignKeyViolation is raised.

    Errors:
        AssertionError if ForeignKeyViolation is not raised.
    """
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                cur.execute(
                    "INSERT INTO exposures (experiment_key, shop_id, variant) VALUES (%s, %s, %s);",
                    ("nonexistent-exp-key", "shop-00001", "A"),
                )
