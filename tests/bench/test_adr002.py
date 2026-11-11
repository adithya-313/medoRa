"""Unit tests for ADR-002 feature flag benchmark harness and algorithms."""

from __future__ import annotations

import math

import pytest

from bench.adr002.homemade import assign_variant
from bench.adr002.run_benchmark import chi_square_50_50, select_winner

pytestmark = pytest.mark.unit


def test_assign_variant_determinism() -> None:
    """Repeated calls for the identical experiment and shop ID yield identical results."""
    first = assign_variant("test-exp", "shop-9999", 50)
    second = assign_variant("test-exp", "shop-9999", 50)
    assert first == second


def test_assign_variant_split_boundaries() -> None:
    """Split 0 allocates all to B; split 100 allocates all to A."""
    sample_shops = [f"shop-{i:04d}" for i in range(50)]
    for sid in sample_shops:
        assert assign_variant("boundary-exp", sid, 0) == "B"
        assert assign_variant("boundary-exp", sid, 100) == "A"


def test_assign_variant_invalid_split_raises() -> None:
    """Out-of-range split percentages raise ValueError."""
    with pytest.raises(ValueError, match="0..100"):
        assign_variant("exp", "shop-1", -1)

    with pytest.raises(ValueError, match="0..100"):
        assign_variant("exp", "shop-1", 101)


def test_assign_variant_known_answers_pinned() -> None:
    """Verify pinned known answers for three fixed shop IDs."""
    exp_key = "adr002-aa"
    split = 50
    assert assign_variant(exp_key, "shop-00001", split) == "B"
    assert assign_variant(exp_key, "shop-00002", split) == "A"
    assert assign_variant(exp_key, "shop-00003", split) == "B"


def test_chi_square_known_inputs() -> None:
    """Verify chi-square p-value formula against known theoretical values."""
    # Balanced 5000/5000: chi2 = 0, p-value = erfc(0) = 1.0
    assert chi_square_50_50(5000, 5000) == 1.0

    # Hand calculation for 5200 / 4800 with 50/50 expected split:
    # N = 10000, expected E_A = 5000, E_B = 5000
    # (O_A - E_A)^2 / E_A = (5200 - 5000)^2 / 5000 = 200^2 / 5000 = 40000 / 5000 = 8.0
    # (O_B - E_B)^2 / E_B = (4800 - 5000)^2 / 5000 = (-200)^2 / 5000 = 40000 / 5000 = 8.0
    # chi2 = 8.0 + 8.0 = 16.0
    # For 1 degree of freedom, p = erfc(sqrt(chi2 / 2)) = erfc(sqrt(16.0 / 2.0)) = erfc(sqrt(8.0))
    # erfc(sqrt(8.0)) = 6.334248366623978e-05
    expected_p = 6.334248366623978e-05
    actual_p = chi_square_50_50(5200, 4800)
    assert math.isclose(actual_p, expected_p, rel_tol=1e-12)


def test_select_winner_decision_rule() -> None:
    """Verify winner selection under hard rule filtering and tie-break rules."""
    # Disqualified candidate fails hard rule despite lower metrics
    fake_disqualified = {
        "candidate-fast-but-disqualified": {
            "H1": True,
            "H2": False,  # fails offline evaluation
            "H3": True,
            "H4": True,
            "H5": True,
            "M1": 0,
            "M2": 10.0,
            "M3": 1.0,
        },
        "candidate-valid": {
            "H1": True,
            "H2": True,
            "H3": True,
            "H4": True,
            "H5": True,
            "M1": 1,
            "M2": 50.0,
            "M3": 15.0,
        },
    }
    assert select_winner(fake_disqualified) == "candidate-valid"

    # Tie on M1 -> lower M2 wins
    fake_tie_m1 = {
        "candidate-higher-ram": {
            "H1": True,
            "H2": True,
            "H3": True,
            "H4": True,
            "H5": True,
            "M1": 1,
            "M2": 120.0,
            "M3": 10.0,
        },
        "candidate-lower-ram": {
            "H1": True,
            "H2": True,
            "H3": True,
            "H4": True,
            "H5": True,
            "M1": 1,
            "M2": 60.0,
            "M3": 25.0,
        },
    }
    assert select_winner(fake_tie_m1) == "candidate-lower-ram"

    # Tie on M1 and M2 -> lower M3 wins
    fake_tie_m2 = {
        "candidate-slower-eval": {
            "H1": True,
            "H2": True,
            "H3": True,
            "H4": True,
            "H5": True,
            "M1": 1,
            "M2": 60.0,
            "M3": 20.0,
        },
        "candidate-faster-eval": {
            "H1": True,
            "H2": True,
            "H3": True,
            "H4": True,
            "H5": True,
            "M1": 1,
            "M2": 60.0,
            "M3": 5.0,
        },
    }
    assert select_winner(fake_tie_m2) == "candidate-faster-eval"
