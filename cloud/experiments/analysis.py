"""Statistical analysis functions for A/B/n experiments and A/A testing."""

from __future__ import annotations

import math


def _validate_srm_inputs(observed: list[int], expected_weights: list[int]) -> None:
    """Validate structure and values of observed counts and expected weights.

    Purpose:
        Ensures valid list inputs, length equality, minimum variants, non-negativity,
        and positive sums.

    Inputs/Outputs:
        observed: List of observed counts per variant.
        expected_weights: List of expected weight values.

    Errors:
        ValueError: If any validation condition is violated.
    """
    if not isinstance(observed, list) or not isinstance(expected_weights, list):
        raise ValueError("observed and expected_weights must be lists of integers")
    if len(observed) != len(expected_weights):
        raise ValueError(f"Length mismatch: {len(observed)} observed vs {len(expected_weights)} weights")
    if len(observed) < 2:
        raise ValueError("SRM test requires at least 2 variants")

    for val in observed:
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            raise ValueError(f"Observed counts must be non-negative integers, got {val!r}")
    for val in expected_weights:
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            raise ValueError(f"Expected weights must be non-negative integers, got {val!r}")

    if sum(observed) == 0:
        raise ValueError("Total observed count must be greater than zero")
    if sum(expected_weights) == 0:
        raise ValueError("Total expected weight must be greater than zero")


def srm_p_value(observed: list[int], expected_weights: list[int]) -> float:
    """Calculate the p-value for a Sample Ratio Mismatch (SRM) test.

    Purpose:
        Tests whether observed assignment counts diverge significantly from expected
        variant weights using a chi-square goodness-of-fit test.

    Inputs/Outputs:
        observed: List of observed counts per variant.
        expected_weights: List of expected weight values (e.g. basis points).
        Returns: Two-tailed p-value in range [0.0, 1.0].

    Key Logic:
        Validates argument structure via _validate_srm_inputs.
        For k=2 variants, computes chi-square with 1 degree of freedom and obtains
        the exact p-value via math.erfc(sqrt(chi2 / 2)).
        For k > 2 variants, raises NotImplementedError.

    Errors:
        ValueError: On mismatched lengths, invalid counts, zero totals, or length < 2.
        NotImplementedError: If variant count k > 2.
    """
    _validate_srm_inputs(observed, expected_weights)

    if len(observed) > 2:
        raise NotImplementedError("SRM p-value currently only supports k=2 variants")

    total_observed = sum(observed)
    total_weight = sum(expected_weights)

    # For k=2, degrees of freedom = 1.
    e0 = total_observed * (expected_weights[0] / total_weight)
    e1 = total_observed * (expected_weights[1] / total_weight)
    if e0 == 0 or e1 == 0:
        raise ValueError("Expected counts cannot be zero")

    chi2 = ((observed[0] - e0) ** 2) / e0 + ((observed[1] - e1) ** 2) / e1
    if chi2 == 0.0:
        return 1.0

    # Chi-square with 1 DOF corresponds to standard normal Z^2; p-value is erfc(sqrt(chi2 / 2)).
    return float(math.erfc(math.sqrt(chi2 / 2.0)))


def welch_p_value(
    mean_a: float,
    var_a: float,
    n_a: int,
    mean_b: float,
    var_b: float,
    n_b: int,
) -> float:
    """Calculate the p-value for a two-sample Welch's t-test using normal approximation.

    Purpose:
        Evaluates the difference in sample means between two independent variants
        with unequal variances, using normal approximation for sample sizes >= 30.

    Inputs/Outputs:
        mean_a: Sample mean of variant A.
        var_a: Sample variance (unbiased) of variant A.
        n_a: Sample size of variant A (must be >= 30).
        mean_b: Sample mean of variant B.
        var_b: Sample variance (unbiased) of variant B.
        n_b: Sample size of variant B (must be >= 30).
        Returns: Two-tailed p-value in range [0.0, 1.0].

    Key Logic:
        Validates minimum sample sizes (>= 30) and non-negative variances.
        Computes standard error of the difference: sqrt(var_a / n_a + var_b / n_b).
        Derives test statistic t = |mean_a - mean_b| / se.
        Applies standard normal two-tailed tail probability via math.erfc(t / sqrt(2)).

    Errors:
        ValueError: If n_a < 30, n_b < 30, or any variance is negative.
    """
    if not isinstance(n_a, int) or isinstance(n_a, bool) or n_a < 30:
        raise ValueError(f"Sample size n_a must be an integer >= 30, got {n_a!r}")
    if not isinstance(n_b, int) or isinstance(n_b, bool) or n_b < 30:
        raise ValueError(f"Sample size n_b must be an integer >= 30, got {n_b!r}")
    if not isinstance(var_a, (int, float)) or var_a < 0:
        raise ValueError(f"Variance var_a must be non-negative, got {var_a!r}")
    if not isinstance(var_b, (int, float)) or var_b < 0:
        raise ValueError(f"Variance var_b must be non-negative, got {var_b!r}")
    if not isinstance(mean_a, (int, float)):
        raise ValueError(f"mean_a must be numeric, got {mean_a!r}")
    if not isinstance(mean_b, (int, float)):
        raise ValueError(f"mean_b must be numeric, got {mean_b!r}")

    se = math.sqrt((var_a / n_a) + (var_b / n_b))
    if se == 0.0:
        # Both variances zero: identical means yield p=1.0, distinct means yield p=0.0.
        return 1.0 if mean_a == mean_b else 0.0

    t_stat = abs(mean_a - mean_b) / se
    return float(math.erfc(t_stat / math.sqrt(2.0)))
