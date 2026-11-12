"""A/A experiment simulation and statistical validation harness (10,000 shops)."""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from testcontainers.community.postgres import PostgresContainer

from cloud.experiments.analysis import srm_p_value, welch_p_value
from cloud.experiments.assignment import assign_variant
from cloud.experiments.store import apply_schema, create_experiment, log_exposure

SEED = 20261112
POSTGRES_IMAGE = "postgres:17-alpine"
NUM_SHOPS = 10000
NUM_REPETITIONS = 1000


def get_current_git_commit() -> str:
    """Retrieve the current HEAD git commit hash."""
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
        return out.decode("utf-8").strip()
    except Exception:
        return "unknown"


def generate_shop_outcomes(seed: int, shops: list[str]) -> dict[str, float]:
    """Generate deterministic outcomes for each shop from a lognormal distribution.

    Purpose:
        Produces identical outcome per shop across runs and repetitions,
        independent of variant allocation.

    Inputs/Outputs:
        seed: Random seed integer.
        shops: List of unique shop ID strings.
        Returns: Dict mapping shop_id to simulated metric value.

    Key Logic:
        Instantiates independent random.Random(f"{seed}:{shop_id}") per shop.
        Draws from lognormvariate(8.0, 0.5).
    """
    outcomes: dict[str, float] = {}
    for shop_id in shops:
        # Seeding per shop guarantees invariance across variants and experiment keys.
        rng = random.Random(f"{seed}:{shop_id}")
        outcomes[shop_id] = rng.lognormvariate(8.0, 0.5)
    return outcomes


def _sample_mean_and_variance(values: list[float]) -> tuple[float, float, int]:
    """Calculate arithmetic mean and unbiased sample variance."""
    n = len(values)
    if n < 30:
        raise ValueError(f"At least 30 samples required, got {n}")
    mean = sum(values) / n
    # Bessel's correction (n - 1) for unbiased sample variance.
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    return float(mean), float(var), n


def run_single_experiment(
    shops: list[str],
    outcomes: dict[str, float],
    conn: psycopg.Connection[Any] | None = None,
) -> dict[str, Any]:
    """Execute single A/A experiment 'aa-single' over 10,000 shops.

    Purpose:
        Evaluates single-run SRM and Welch invariance, querying from PostgreSQL
        when database connection is provided.

    Inputs/Outputs:
        shops: 10,000 shop IDs.
        outcomes: Precalculated outcomes mapping.
        conn: Optional active database connection.
        Returns: Dict containing single-run metrics, p-values, and pass booleans.
    """
    experiment_key = "aa-single"
    variants: list[tuple[str, int]] = [("A", 5000), ("A-copy", 5000)]

    if conn is not None:
        apply_schema(conn)
        create_experiment(conn, experiment_key, variants, status="running")
        for shop_id in shops:
            log_exposure(conn, experiment_key, shop_id)

        # Read back assignments directly from the database table.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT shop_id, variant FROM exposures WHERE experiment_key = %s ORDER BY shop_id;",
                (experiment_key,),
            )
            rows = cur.fetchall()

        shop_variants: dict[str, str] = {row[0]: row[1] for row in rows}
    else:
        shop_variants = {shop_id: assign_variant(experiment_key, shop_id, variants) for shop_id in shops}

    vals_a: list[float] = []
    vals_ac: list[float] = []

    for shop_id in shops:
        assigned = shop_variants[shop_id]
        val = outcomes[shop_id]
        if assigned == "A":
            vals_a.append(val)
        elif assigned == "A-copy":
            vals_ac.append(val)
        else:
            raise ValueError(f"Unexpected variant {assigned!r}")

    mean_a, var_a, n_a = _sample_mean_and_variance(vals_a)
    mean_ac, var_ac, n_ac = _sample_mean_and_variance(vals_ac)

    srm_p = srm_p_value([n_a, n_ac], [5000, 5000])
    welch_p = welch_p_value(mean_a, var_a, n_a, mean_ac, var_ac, n_ac)

    single_srm_pass = bool(srm_p >= 0.01)
    single_welch_pass = bool(welch_p >= 0.05)
    single_pass = single_srm_pass and single_welch_pass

    return {
        "experiment_key": experiment_key,
        "count_a": n_a,
        "count_a_copy": n_ac,
        "mean_a": round(mean_a, 4),
        "var_a": round(var_a, 4),
        "mean_a_copy": round(mean_ac, 4),
        "var_a_copy": round(var_ac, 4),
        "srm_p_value": round(srm_p, 4),
        "welch_p_value": round(welch_p, 4),
        "single_srm_pass": single_srm_pass,
        "single_welch_pass": single_welch_pass,
        "single_pass": single_pass,
    }


def run_aa_repetitions(
    shops: list[str],
    outcomes: dict[str, float],
    num_reps: int = 1000,
) -> dict[str, Any]:
    """Execute 1,000 in-memory A/A repetitions 'aa-rep-0000'..'aa-rep-0999'.

    Purpose:
        Verifies empirical Type I error calibration across 1,000 repetitions.

    Inputs/Outputs:
        shops: 10,000 shop IDs.
        outcomes: Precalculated outcomes mapping.
        num_reps: Total repetitions (default 1000).
        Returns: Dict containing false positive counts, rates, and pass booleans.
    """
    variants: list[tuple[str, int]] = [("A", 5000), ("A-copy", 5000)]
    welch_fp_count = 0
    srm_fp_count = 0

    for rep in range(num_reps):
        exp_key = f"aa-rep-{rep:04d}"

        count_a = 0
        count_ac = 0
        sum_a = 0.0
        sum_ac = 0.0
        sq_sum_a = 0.0
        sq_sum_ac = 0.0

        for shop_id in shops:
            variant = assign_variant(exp_key, shop_id, variants)
            val = outcomes[shop_id]
            if variant == "A":
                count_a += 1
                sum_a += val
                sq_sum_a += val * val
            else:
                count_ac += 1
                sum_ac += val
                sq_sum_ac += val * val

        mean_a = sum_a / count_a
        var_a = (sq_sum_a - (sum_a * sum_a) / count_a) / (count_a - 1)

        mean_ac = sum_ac / count_ac
        var_ac = (sq_sum_ac - (sum_ac * sum_ac) / count_ac) / (count_ac - 1)

        srm_p = srm_p_value([count_a, count_ac], [5000, 5000])
        welch_p = welch_p_value(mean_a, var_a, count_a, mean_ac, var_ac, count_ac)

        if welch_p < 0.05:
            welch_fp_count += 1
        if srm_p < 0.01:
            srm_fp_count += 1

    welch_rate = round(welch_fp_count / num_reps, 4)
    srm_rate = round(srm_fp_count / num_reps, 4)

    repetition_welch_pass = bool(0.029 <= welch_rate <= 0.071)
    repetition_srm_pass = bool(srm_rate <= 0.02)
    repetition_pass = repetition_welch_pass and repetition_srm_pass

    return {
        "num_reps": num_reps,
        "welch_alpha": 0.05,
        "srm_alpha": 0.01,
        "welch_false_positive_count": welch_fp_count,
        "welch_false_positive_rate": welch_rate,
        "srm_false_positive_count": srm_fp_count,
        "srm_false_positive_rate": srm_rate,
        "repetition_welch_pass": repetition_welch_pass,
        "repetition_srm_pass": repetition_srm_pass,
        "repetition_pass": repetition_pass,
    }


def run_and_save(database: bool = False) -> dict[str, Any]:
    """Run full A/A validation suite and write results to aa_test.json."""
    output_dir = Path("ml/experiments/results")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "aa_test.json"

    shops = [f"shop-{i:05d}" for i in range(1, NUM_SHOPS + 1)]
    outcomes = generate_shop_outcomes(SEED, shops)

    if database:
        db_url = os.environ.get("POSTGRES_URL")
        container = None
        if not db_url:
            container = PostgresContainer(POSTGRES_IMAGE, driver=None)
            container.start()
            db_url = container.get_connection_url()

        try:
            with psycopg.connect(db_url, autocommit=True) as conn:
                single_results = run_single_experiment(shops, outcomes, conn=conn)
        finally:
            if container is not None:
                container.stop()
    else:
        single_results = run_single_experiment(shops, outcomes, conn=None)

    rep_results = run_aa_repetitions(shops, outcomes, num_reps=NUM_REPETITIONS)

    overall_pass = single_results["single_pass"] and rep_results["repetition_pass"]

    data: dict[str, Any] = {
        "seed": SEED,
        "n_shops": NUM_SHOPS,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "git_commit": get_current_git_commit(),
        "single_run": single_results,
        "repetitions": rep_results,
        "pass_rules": {
            "single_srm_pass": single_results["single_srm_pass"],
            "single_welch_pass": single_results["single_welch_pass"],
            "repetition_welch_pass": rep_results["repetition_welch_pass"],
            "repetition_srm_pass": rep_results["repetition_srm_pass"],
            "overall_pass": overall_pass,
        },
        "overall_pass": overall_pass,
    }

    output_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run A/A test validation harness.")
    parser.add_argument(
        "--database",
        action="store_true",
        help="Execute single run through PostgreSQL exposure store",
    )
    args = parser.parse_args()

    res = run_and_save(database=args.database)
    print("A/A Test Completed.")
    print(
        f"Single: n_shops={res['n_shops']}, count A={res['single_run']['count_a']}, "
        f"count A-copy={res['single_run']['count_a_copy']}, "
        f"srm p={res['single_run']['srm_p_value']}, welch p={res['single_run']['welch_p_value']}"
    )
    print(
        f"Reps: welch rate={res['repetitions']['welch_false_positive_rate']:.4f}, "
        f"srm rate={res['repetitions']['srm_false_positive_rate']:.4f}"
    )
    print(
        f"Pass booleans: single_srm={res['pass_rules']['single_srm_pass']}, "
        f"single_welch={res['pass_rules']['single_welch_pass']}, "
        f"rep_welch={res['pass_rules']['repetition_welch_pass']}, "
        f"rep_srm={res['pass_rules']['repetition_srm_pass']}, "
        f"overall={res['overall_pass']}"
    )
