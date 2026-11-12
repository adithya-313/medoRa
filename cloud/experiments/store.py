"""PostgreSQL storage operations for feature flags, experiments, and exposures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg

from cloud.experiments.assignment import assign_variant

VALID_EXPERIMENT_STATUSES = ("draft", "running", "stopped")


def apply_schema(conn: psycopg.Connection[Any]) -> None:
    """Apply the experiments and feature flags SQL schema to the database.

    Purpose:
        Executes schema.sql DDL to create tables and partial indexes idempotently.

    Inputs/Outputs:
        conn: An active psycopg Connection instance.

    Key Logic:
        Reads schema.sql adjacent to this module and executes the DDL script.

    Errors:
        psycopg.Error: On database execution failure.
    """
    schema_path = Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)


def set_flag(
    conn: psycopg.Connection[Any],
    flag_key: str,
    enabled: bool,
    shop_id: str | None = None,
) -> None:
    """Set or update a global or shop-specific feature flag.

    Purpose:
        Inserts or updates the enabled state of a feature flag.

    Inputs/Outputs:
        conn: Active psycopg Connection.
        flag_key: Identifier string of the feature flag.
        enabled: Boolean flag state.
        shop_id: Optional shop identifier for targeted overrides.

    Key Logic:
        Validates argument types and non-empty strings.
        Performs an upsert targeting the respective partial unique index:
        uq_feature_flags_global when shop_id IS NULL, or uq_feature_flags_shop
        when shop_id is provided.

    Errors:
        ValueError: If flag_key is empty/invalid, enabled is not bool, or shop_id is invalid.
        psycopg.Error: On database execution failure.
    """
    if not isinstance(flag_key, str) or not flag_key:
        raise ValueError("flag_key must be a non-empty string")
    if not isinstance(enabled, bool):
        raise ValueError(f"enabled must be a boolean, got {enabled!r}")
    if shop_id is not None and (not isinstance(shop_id, str) or not shop_id):
        raise ValueError("shop_id must be a non-empty string when provided")

    with conn.cursor() as cur:
        if shop_id is None:
            # Upsert into global flag row constrained by uq_feature_flags_global index.
            cur.execute(
                """
                INSERT INTO feature_flags (flag_key, shop_id, enabled, updated_at)
                VALUES (%s, NULL, %s, NOW())
                ON CONFLICT (flag_key) WHERE shop_id IS NULL
                DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = NOW();
                """,
                (flag_key, enabled),
            )
        else:
            # Upsert into shop-specific override row constrained by uq_feature_flags_shop index.
            cur.execute(
                """
                INSERT INTO feature_flags (flag_key, shop_id, enabled, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (flag_key, shop_id) WHERE shop_id IS NOT NULL
                DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = NOW();
                """,
                (flag_key, shop_id, enabled),
            )


def is_flag_enabled(
    conn: psycopg.Connection[Any],
    flag_key: str,
    shop_id: str | None = None,
) -> bool:
    """Check whether a feature flag is enabled for a given shop or globally.

    Purpose:
        Evaluates flag status with shop-specific override precedence over global.
        If the flag is unknown (not configured), returns False per specification.

    Inputs/Outputs:
        conn: Active psycopg Connection.
        flag_key: Identifier string of the feature flag.
        shop_id: Optional shop identifier.
        Returns: Boolean representing flag status (False if unknown).

    Key Logic:
        If shop_id is provided, first checks for a shop-specific override.
        If no shop-specific override exists, checks for a global flag setting.
        If no flag record exists in the store, returns False per specification.

    Errors:
        ValueError: If flag_key is empty/invalid, or shop_id is empty/invalid.
        psycopg.Error: On database execution failure.
    """
    if not isinstance(flag_key, str) or not flag_key:
        raise ValueError("flag_key must be a non-empty string")
    if shop_id is not None and (not isinstance(shop_id, str) or not shop_id):
        raise ValueError("shop_id must be a non-empty string when provided")

    with conn.cursor() as cur:
        if shop_id is not None:
            # 1. Check for shop-level override.
            cur.execute(
                "SELECT enabled FROM feature_flags WHERE flag_key = %s AND shop_id = %s;",
                (flag_key, shop_id),
            )
            row = cur.fetchone()
            if row is not None:
                return bool(row[0])

        # 2. Check for global flag definition.
        cur.execute(
            "SELECT enabled FROM feature_flags WHERE flag_key = %s AND shop_id IS NULL;",
            (flag_key,),
        )
        row = cur.fetchone()
        if row is not None:
            return bool(row[0])

    # Unknown flag returns False per specification.
    return False


def _validate_variants(variants: list[tuple[str, int]]) -> None:
    """Validate structure and basis-point weights of experiment variants."""
    if not isinstance(variants, list) or not variants:
        raise ValueError("variants must be a non-empty list of (name, weight) tuples")

    seen_names: set[str] = set()
    total_weight = 0
    for item in variants:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError(f"Each variant must be a 2-tuple (name, weight), got {item!r}")
        name, weight = item
        if not isinstance(name, str) or not name:
            raise ValueError(f"Variant name must be a non-empty string, got {name!r}")
        if name in seen_names:
            raise ValueError(f"Duplicate variant name: {name!r}")
        seen_names.add(name)
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 0:
            raise ValueError(f"Variant weight must be a non-negative integer, got {weight!r}")
        total_weight += weight

    if total_weight != 10000:
        raise ValueError(f"Variant weights must sum to exactly 10000 basis points, got {total_weight}")


def create_experiment(
    conn: psycopg.Connection[Any],
    experiment_key: str,
    variants: list[tuple[str, int]],
    status: str,
) -> None:
    """Register a new experiment configuration in the database.

    Purpose:
        Validates and records experiment variant definitions and lifecycle status.

    Inputs/Outputs:
        conn: Active psycopg Connection.
        experiment_key: Unique experiment identifier.
        variants: List of (variant_name, weight_basis_points) tuples.
        status: Lifecycle status string ('draft', 'running', or 'stopped').

    Key Logic:
        Validates experiment_key, status, and variant weights (sum == 10000).
        Inserts serialized JSON representation and status into experiments table.

    Errors:
        ValueError: On invalid key, unknown status, or invalid variant configuration.
        psycopg.Error: On constraint violation (e.g. duplicate key) or DB error.
    """
    if not isinstance(experiment_key, str) or not experiment_key:
        raise ValueError("experiment_key must be a non-empty string")
    if not isinstance(status, str) or status not in VALID_EXPERIMENT_STATUSES:
        raise ValueError(f"status must be one of {VALID_EXPERIMENT_STATUSES!r}, got {status!r}")
    _validate_variants(variants)

    # Store variants list as json string into jsonb column.
    variants_json = json.dumps(variants)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO experiments (experiment_key, variants, status) VALUES (%s, %s, %s);",
            (experiment_key, variants_json, status),
        )


def log_exposure(
    conn: psycopg.Connection[Any],
    experiment_key: str,
    shop_id: str,
) -> str:
    """Record an experiment exposure and return the assigned variant.

    Purpose:
        Determines and logs a shop's exposure to an active experiment idempotently.

    Inputs/Outputs:
        conn: Active psycopg Connection.
        experiment_key: Experiment identifier string.
        shop_id: Shop identifier string.
        Returns: The assigned and stored variant name string.

    Key Logic:
        Fetches experiment variants and status from the database.
        Raises ValueError if experiment does not exist or status != 'running'.
        Computes the variant deterministically using assign_variant.
        Inserts the exposure record with ON CONFLICT (experiment_key, shop_id) DO NOTHING.
        Returns the stored variant (from RETURNING or fallback select on conflict).

    Errors:
        ValueError: If arguments are invalid, experiment is not found, or status != 'running'.
        psycopg.Error: On database execution failure.
    """
    if not isinstance(experiment_key, str) or not experiment_key:
        raise ValueError("experiment_key must be a non-empty string")
    if not isinstance(shop_id, str) or not shop_id:
        raise ValueError("shop_id must be a non-empty string")

    with conn.cursor() as cur:
        # Load experiment variants and status from the database.
        cur.execute(
            "SELECT variants, status FROM experiments WHERE experiment_key = %s;",
            (experiment_key,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"Experiment '{experiment_key}' does not exist")

        variants_data, status = row
        if status != "running":
            raise ValueError(f"Experiment '{experiment_key}' is not running (status: '{status}')")

        if isinstance(variants_data, str):
            raw_variants = json.loads(variants_data)
        else:
            raw_variants = variants_data
        variants: list[tuple[str, int]] = [(v[0], int(v[1])) for v in raw_variants]

        assigned = assign_variant(experiment_key, shop_id, variants)

        # Idempotent insert: if already exposed, do nothing and query stored variant.
        cur.execute(
            """
            INSERT INTO exposures (experiment_key, shop_id, variant)
            VALUES (%s, %s, %s)
            ON CONFLICT (experiment_key, shop_id) DO NOTHING
            RETURNING variant;
            """,
            (experiment_key, shop_id, assigned),
        )
        insert_row = cur.fetchone()
        if insert_row is not None:
            return str(insert_row[0])

        cur.execute(
            "SELECT variant FROM exposures WHERE experiment_key = %s AND shop_id = %s;",
            (experiment_key, shop_id),
        )
        existing_row = cur.fetchone()
        if existing_row is not None:
            return str(existing_row[0])

    raise RuntimeError(f"Failed to retrieve exposure record for {experiment_key}:{shop_id}")
