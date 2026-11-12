"""Unit tests for statistical experiment analysis functions."""

from __future__ import annotations

import math

import pytest

from cloud.experiments.analysis import srm_p_value, welch_p_value

pytestmark = pytest.mark.unit


def test_srm_p_value_known_values() -> None:
    """Verify SRM p-value calculations against known statistical values.

    Purpose:
        Confirms goodness-of-fit chi-square p-values for balanced, unbalanced, and unequal splits.

    Inputs/Outputs:
        Various 2-variant observed counts and expected weights.

    Key Logic:
        Asserts exact match (p=1.0), borderline variation (p ~ 0.317), severe mismatch (p < 1e-20),
        and proportional splits (e.g. 70/30).

    Errors:
        AssertionError if computed p-values diverge from statistical expectations.
    """
    # Perfectly balanced counts yield p-value of 1.0.
    assert srm_p_value([5000, 5000], [5000, 5000]) == 1.0

    # Slight variation: chi2 = 1.0, Z = 1.0 -> p = erfc(1 / sqrt(2)) ~ 0.31731
    p_slight = srm_p_value([5050, 4950], [5000, 5000])
    expected_slight = math.erfc(1.0 / math.sqrt(2.0))
    assert math.isclose(p_slight, expected_slight, rel_tol=1e-6)

    # Severe mismatch (5500 vs 4500, chi2 = 100, Z = 10) -> p < 1e-20
    p_severe = srm_p_value([5500, 4500], [5000, 5000])
    assert p_severe < 1e-20

    # Unequal expected weights (70/30) matching observed counts
    assert srm_p_value([700, 300], [7000, 3000]) == 1.0


def test_srm_p_value_validation_and_unsupported() -> None:
    """Verify input validation and unsupported variant counts in SRM testing.

    Purpose:
        Ensures strict argument checks and expected NotImplementedError for k > 2.

    Inputs/Outputs:
        Invalid inputs and k=3 configuration.

    Key Logic:
        Asserts ValueError on invalid types, negative counts, zero sums, and length mismatches.
        Asserts NotImplementedError when len(variants) > 2.

    Errors:
        AssertionError if expected exceptions are not raised.
    """
    # Unsupported k > 2
    with pytest.raises(NotImplementedError, match="only supports k=2"):
        srm_p_value([333, 333, 334], [3333, 3333, 3334])

    # Less than 2 variants
    with pytest.raises(ValueError, match="at least 2"):
        srm_p_value([100], [10000])

    # Length mismatch
    with pytest.raises(ValueError, match="Length mismatch"):
        srm_p_value([500, 500], [5000])

    # Negative counts or weights
    with pytest.raises(ValueError, match="non-negative integer"):
        srm_p_value([-1, 500], [5000, 5000])
    with pytest.raises(ValueError, match="non-negative integer"):
        srm_p_value([500, 500], [5000, -10])

    # Zero total count or weight
    with pytest.raises(ValueError, match="Total observed count"):
        srm_p_value([0, 0], [5000, 5000])
    with pytest.raises(ValueError, match="Total expected weight"):
        srm_p_value([500, 500], [0, 0])


def test_welch_p_value_known_values() -> None:
    """Verify Welch's t-test p-value calculations against known statistical benchmarks.

    Purpose:
        Confirms two-sample difference testing under normal approximation for N >= 30.

    Inputs/Outputs:
        Means, variances, and sample sizes for two variants.

    Key Logic:
        Asserts identical means yield p=1.0.
        Asserts large separation yields tiny p-value.
        Asserts critical value z=1.959964 yields p ~ 0.05.

    Errors:
        AssertionError if computed p-values diverge from expectations.
    """
    # Identical means and variances yield p=1.0.
    assert welch_p_value(100.0, 10.0, 50, 100.0, 10.0, 50) == 1.0

    # Known two-tailed critical value for alpha=0.05:
    # Let se = 1.0 (var_a=100, n_a=200, var_b=100, n_b=200 -> se = sqrt(0.5 + 0.5) = 1.0)
    # diff = 1.95996398454 -> t = 1.95996398454 -> p ~ 0.05
    z_05 = 1.95996398454
    p_val_05 = welch_p_value(0.0, 100.0, 200, z_05, 100.0, 200)
    assert math.isclose(p_val_05, 0.05, rel_tol=1e-5)

    # Highly significant difference yields very small p-value
    p_sig = welch_p_value(10.0, 2.0, 100, 20.0, 2.0, 100)
    assert p_sig < 1e-15


def test_welch_p_value_validation_and_edge_cases() -> None:
    """Verify validation constraints and zero-variance edge cases in Welch's test.

    Purpose:
        Ensures sample size minimums (N >= 30) and valid variance checks are enforced.

    Inputs/Outputs:
        Small sample sizes (< 30), negative variances, and zero-variance scenarios.

    Key Logic:
        Checks ValueError for n < 30 and negative variances.
        Checks zero variance behavior: p=1.0 if means equal, p=0.0 if means differ.

    Errors:
        AssertionError if validation fails or zero-variance behavior is incorrect.
    """
    # Sample size < 30
    with pytest.raises(ValueError, match="n_a must be an integer >= 30"):
        welch_p_value(10.0, 1.0, 29, 10.0, 1.0, 50)
    with pytest.raises(ValueError, match="n_b must be an integer >= 30"):
        welch_p_value(10.0, 1.0, 50, 10.0, 1.0, 15)

    # Negative variance
    with pytest.raises(ValueError, match="var_a must be non-negative"):
        welch_p_value(10.0, -0.5, 50, 10.0, 1.0, 50)
    with pytest.raises(ValueError, match="var_b must be non-negative"):
        welch_p_value(10.0, 1.0, 50, 10.0, -1.0, 50)

    # Zero variance edge cases
    assert welch_p_value(5.0, 0.0, 40, 5.0, 0.0, 40) == 1.0
    assert welch_p_value(5.0, 0.0, 40, 6.0, 0.0, 40) == 0.0
