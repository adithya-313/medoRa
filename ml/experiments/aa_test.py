"""A/A experiment simulation, statistical validation, and false-positive rate evaluation."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from pathlib import Path
from typing import Any

import psycopg
from testcontainers.community.postgres import PostgresContainer

from cloud.experiments.analysis import srm_p_value, welch_p_value
from cloud.experiments.assignment import assign_variant
from cloud.experiments.store import apply_schema, create_experiment, log_exposure

SEED = 20261112
POSTGRES_IMAGE = "postgres:17-alpine"


def compute_sample_stats(values: list[float]) -> tuple[float, float, int]:
    """Compute sample mean and unbiased sample variance for a list of values.

    Purpose:
        Provides sample descriptive statistics required for two-sample Welch's t-test.

    Inputs/Outputs:
        values: Non-empty list of floating point values (length >= 30).
        Returns: Tuple of (mean, sample_variance, count).

    Key Logic:
        Calculates arithmetic mean, then computes sum of squared deviations
        divided by (n - 1) for Bessel's correction.

    Errors:
        ValueError: If values contains fewer than 30 items.
    """
    n = len(values)
    if n < 30:
        raise ValueError(f"At least 30 values required for sample statistics, got {n}")

    mean = sum(values) / n
    # Bessel's correction (n - 1) provides unbiased sample variance.
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    return float(mean), float(var), n


def run_single_experiment(
    conn: psycopg.Connection[Any] | None = None,
    num_shops: int = 1000,
) -> dict[str, Any]:
    """Run a single simulated A/A experiment and evaluate SRM and Welch p-values.

    Purpose:
        Simulates an A/A experiment over a cohort of shops, optionally persisting
        assignments and exposures to PostgreSQL, and calculates statistical metrics.

    Inputs/Outputs:
        conn: Optional active psycopg Connection to persist exposures.
        num_shops: Total number of shops to simulate (default 1000).
        Returns: Dictionary of single-run metrics and p-values.

    Key Logic:
        Assigns shops to variant 'A' or 'B' (50/50 split).
        Generates identically distributed lognormal metric values for all shops.
        Optionally persists experiment definition and exposure records in Postgres.
        Computes sample stats and tests for SRM and difference in means.

    Errors:
        ValueError: On invalid shop counts or statistical calculation error.
    """
    rng = random.Random(SEED)
    experiment_key = "aa-test-single"
    variants = [("A", 5000), ("B", 5000)]
    shop_ids = [f"shop-{i:05d}" for i in range(1, num_shops + 1)]

    if conn is not None:
        apply_schema(conn)
        create_experiment(conn, experiment_key, variants)

    vals_a: list[float] = []
    vals_b: list[float] = []

    for shop_id in shop_ids:
        variant = assign_variant(experiment_key, shop_id, variants)
        if conn is not None:
            log_exposure(conn, experiment_key, shop_id, variant)

        # Lognormal distribution represents typical skewed e-commerce metrics (order volume, GMV).
        metric_val = math.exp(rng.gauss(4.5, 0.5))
        if variant == "A":
            vals_a.append(metric_val)
        else:
            vals_b.append(metric_val)

    mean_a, var_a, n_a = compute_sample_stats(vals_a)
    mean_b, var_b, n_b = compute_sample_stats(vals_b)

    srm_p = srm_p_value([n_a, n_b], [5000, 5000])
    welch_p = welch_p_value(mean_a, var_a, n_a, mean_b, var_b, n_b)

    return {
        "experiment_key": experiment_key,
        "n_a": n_a,
        "n_b": n_b,
        "mean_a": round(mean_a, 4),
        "var_a": round(var_a, 4),
        "mean_b": round(mean_b, 4),
        "var_b": round(var_b, 4),
        "srm_p_value": round(srm_p, 4),
        "welch_p_value": round(welch_p, 4),
    }


def run_aa_repetitions(
    num_reps: int = 1000,
    num_shops: int = 1000,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Simulate repeated A/A experiments to verify nominal false positive rate.

    Purpose:
        Validates that across 1,000 independent A/A experiments, the Type I error
        rate (false positive rate at alpha=0.05) is consistent with statistical theory (~5%).

    Inputs/Outputs:
        num_reps: Number of repetitions to run (default 1000).
        num_shops: Number of shops per experiment (default 1000).
        alpha: Significance threshold (default 0.05).
        Returns: Summary dictionary containing empirical false positive rates.

    Key Logic:
        Iterates over repetitions using an independent seed per repetition.
        Evaluates Welch p-value and SRM p-value for each iteration.
        Calculates the proportion of experiments where p < alpha.

    Errors:
        ValueError: On invalid iteration or shop counts.
    """
    variants = [("A", 5000), ("B", 5000)]
    welch_false_positives = 0
    srm_false_positives = 0

    for rep in range(num_reps):
        rng = random.Random(SEED + rep + 1)
        experiment_key = f"aa-rep-{rep:04d}"

        vals_a: list[float] = []
        vals_b: list[float] = []

        for i in range(1, num_shops + 1):
            shop_id = f"shop-{i:05d}"
            variant = assign_variant(experiment_key, shop_id, variants)
            metric_val = math.exp(rng.gauss(4.5, 0.5))
            if variant == "A":
                vals_a.append(metric_val)
            else:
                vals_b.append(metric_val)

        mean_a, var_a, n_a = compute_sample_stats(vals_a)
        mean_b, var_b, n_b = compute_sample_stats(vals_b)

        srm_p = srm_p_value([n_a, n_b], [5000, 5000])
        welch_p = welch_p_value(mean_a, var_a, n_a, mean_b, var_b, n_b)

        if welch_p < alpha:
            welch_false_positives += 1
        if srm_p < alpha:
            srm_false_positives += 1

    return {
        "num_reps": num_reps,
        "num_shops_per_rep": num_shops,
        "alpha": alpha,
        "welch_false_positive_count": welch_false_positives,
        "welch_false_positive_rate": round(welch_false_positives / num_reps, 4),
        "srm_false_positive_count": srm_false_positives,
        "srm_false_positive_rate": round(srm_false_positives / num_reps, 4),
    }


def run_and_save(database: bool = False) -> dict[str, Any]:
    """Execute A/A simulation harness and save results JSON.

    Purpose:
        Executes single experiment and multi-repetition simulation, writing
        ml/experiments/results/aa_test.json.

    Inputs/Outputs:
        database: Whether to persist single experiment to PostgreSQL.
        Returns: Full results dictionary.

    Key Logic:
        If database=True, connects to Postgres (or starts testcontainer if not set).
        Executes single run, then 1000 repetitions.
        Saves output JSON file.

    Errors:
        RuntimeError: If database connection fails.
    """
    output_dir = Path("ml/experiments/results")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "aa_test.json"

    if database:
        db_url = os.environ.get("POSTGRES_URL")
        container = None
        if not db_url:
            container = PostgresContainer(POSTGRES_IMAGE, driver=None)
            container.start()
            db_url = container.get_connection_url()

        try:
            with psycopg.connect(db_url, autocommit=True) as conn:
                single_results = run_single_experiment(conn=conn)
        finally:
            if container is not None:
                container.stop()
    else:
        single_results = run_single_experiment(conn=None)

    reps_results = run_aa_repetitions(num_reps=1000, num_shops=1000, alpha=0.05)

    full_results = {
        "seed": SEED,
        "single_run": single_results,
        "repetitions": reps_results,
    }

    output_path.write_text(json.dumps(full_results, indent=2) + "\n", encoding="utf-8")
    return full_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run A/A test simulation and validation.")
    parser.add_argument(
        "--database",
        action="store_true",
        help="Persist single-run experiment and exposures to PostgreSQL",
    )
    args = parser.parse_args()

    results = run_and_save(database=args.database)
    print("A/A Test Simulation Complete.")
    print(f"Single run: A={results['single_run']['n_a']}, B={results['single_run']['n_b']}")
    print(f"  SRM p-value: {results['single_run']['srm_p_value']}")
    print(f"  Welch p-value: {results['single_run']['welch_p_value']}")
    print("Repetitions (1000 runs):")
    print(f"  Welch false positive rate: {results['repetitions']['welch_false_positive_rate']:.2%}")
    print(f"  SRM false positive rate: {results['repetitions']['srm_false_positive_rate']:.2%}")
    print("Saved results to ml/experiments/results/aa_test.json")
