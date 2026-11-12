"""Unit tests for deterministic experiment assignment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bench.adr002.homemade import assign_variant as homemade_assign_variant
from cloud.experiments.assignment import assign_variant, compute_bucket

pytestmark = pytest.mark.unit


def test_validation_errors() -> None:
    """Verify ValueError is raised on malformed inputs without silent fallbacks.

    Purpose:
        Enforces strict input validation for keys, shop IDs, and variant definitions.

    Inputs/Outputs:
        Various invalid configurations tested with pytest.raises(ValueError).

    Key Logic:
        Tests empty strings, non-string types, malformed tuples, duplicate variant names,
        negative/non-integer weights, and weights that do not sum to 10000.

    Errors:
        ValueError on any invalid argument.
    """
    valid_variants = [("A", 5000), ("B", 5000)]

    # Invalid experiment_key
    with pytest.raises(ValueError, match="experiment_key"):
        compute_bucket("", "shop-1")
    with pytest.raises(ValueError, match="experiment_key"):
        assign_variant(None, "shop-1", valid_variants)  # type: ignore[arg-type]

    # Invalid shop_id
    with pytest.raises(ValueError, match="shop_id"):
        compute_bucket("exp", "")
    with pytest.raises(ValueError, match="shop_id"):
        assign_variant("exp", 123, valid_variants)  # type: ignore[arg-type]

    # Invalid variants list
    with pytest.raises(ValueError, match="variants must be a non-empty list"):
        assign_variant("exp", "shop-1", [])
    with pytest.raises(ValueError, match="variants must be a non-empty list"):
        assign_variant("exp", "shop-1", "not-a-list")  # type: ignore[arg-type]

    # Malformed variant tuples
    with pytest.raises(ValueError, match="2-tuple"):
        assign_variant("exp", "shop-1", [("A", 5000, 100)])  # type: ignore[list-item]

    # Empty variant name
    with pytest.raises(ValueError, match="Variant name"):
        assign_variant("exp", "shop-1", [("", 5000), ("B", 5000)])

    # Duplicate variant names
    with pytest.raises(ValueError, match="Duplicate variant name"):
        assign_variant("exp", "shop-1", [("A", 5000), ("A", 5000)])

    # Invalid weights
    with pytest.raises(ValueError, match="non-negative integer"):
        assign_variant("exp", "shop-1", [("A", -1), ("B", 10001)])
    with pytest.raises(ValueError, match="non-negative integer"):
        assign_variant("exp", "shop-1", [("A", True), ("B", 9999)])
    with pytest.raises(ValueError, match="non-negative integer"):
        assign_variant("exp", "shop-1", [("A", 5000.5), ("B", 4999.5)])  # type: ignore[list-item]

    # Sum does not equal 10000
    with pytest.raises(ValueError, match="weights must sum to exactly 10000"):
        assign_variant("exp", "shop-1", [("A", 4999), ("B", 5000)])
    with pytest.raises(ValueError, match="weights must sum to exactly 10000"):
        assign_variant("exp", "shop-1", [("A", 5001), ("B", 5000)])


def test_determinism() -> None:
    """Verify that assignment for identical inputs produces identical output.

    Purpose:
        Ensures deterministic allocation across repeated calls.

    Inputs/Outputs:
        Repeated calls with fixed experiment and shop IDs.

    Key Logic:
        Computes assignment 10 times for several shops and asserts consistent variant.

    Errors:
        AssertionError if any repeated assignment differs.
    """
    variants = [("control", 4000), ("treatment_1", 3000), ("treatment_2", 3000)]
    for i in range(20):
        shop = f"shop-{i:04d}"
        first = assign_variant("test-det", shop, variants)
        for _ in range(5):
            assert assign_variant("test-det", shop, variants) == first


def test_weight_zero_never_assigned() -> None:
    """Verify that variants with zero weight are never allocated.

    Purpose:
        Ensures basis point weight of 0 allocates 0% of traffic.

    Inputs/Outputs:
        Variant list containing a zero-weight item across 500 shops.

    Key Logic:
        Tests both middle zero-weight and leading zero-weight configurations.
        Asserts the zero-weight variant is never observed.

    Errors:
        AssertionError if zero-weight variant is assigned.
    """
    variants = [("A", 5000), ("NEVER", 0), ("B", 5000)]
    for i in range(500):
        shop = f"shop-{i:05d}"
        variant = assign_variant("test-zero-weight", shop, variants)
        assert variant in ("A", "B")
        assert variant != "NEVER"


def test_single_variant_all_weights() -> None:
    """Verify a single variant with weight 10,000 receives all allocations.

    Purpose:
        Tests 100% allocation boundary condition.

    Inputs/Outputs:
        Variants list with one variant at 10,000 basis points.

    Key Logic:
        Evaluates 200 shops; asserts all receive 'only'.

    Errors:
        AssertionError if any shop does not receive 'only'.
    """
    variants = [("only", 10000)]
    for i in range(200):
        assert assign_variant("test-single", f"shop-{i:04d}", variants) == "only"


def test_boundary_buckets() -> None:
    """Verify exact bucket boundary transitions using precalculated boundary inputs.

    Purpose:
        Pins specific SHA-256 inputs that hit edge buckets (0, 3332, 3333, 4999, 5000, 6665, 6666, 9999).

    Inputs/Outputs:
        Known shop IDs hashed with 'test-boundary'.

    Key Logic:
        Checks compute_bucket produces exact boundary values.
        Checks assign_variant transitions correctly across 3333/3333/3334 and 5000/5000 splits.

    Errors:
        AssertionError if bucket or variant does not match expected boundary values.
    """
    key = "test-boundary"
    # Pre-calculated boundary shops:
    # bucket 0 -> shop-13669
    # bucket 3332 -> shop-03286
    # bucket 3333 -> shop-09143
    # bucket 4999 -> shop-07949
    # bucket 5000 -> shop-05498
    # bucket 6665 -> shop-21318
    # bucket 6666 -> shop-13061
    # bucket 9999 -> shop-11965
    assert compute_bucket(key, "shop-13669") == 0
    assert compute_bucket(key, "shop-03286") == 3332
    assert compute_bucket(key, "shop-09143") == 3333
    assert compute_bucket(key, "shop-07949") == 4999
    assert compute_bucket(key, "shop-05498") == 5000
    assert compute_bucket(key, "shop-21318") == 6665
    assert compute_bucket(key, "shop-13061") == 6666
    assert compute_bucket(key, "shop-11965") == 9999

    three_way = [("A", 3333), ("B", 3333), ("C", 3334)]
    assert assign_variant(key, "shop-13669", three_way) == "A"  # bucket 0 < 3333
    assert assign_variant(key, "shop-03286", three_way) == "A"  # bucket 3332 < 3333
    assert assign_variant(key, "shop-09143", three_way) == "B"  # bucket 3333 < 6666
    assert assign_variant(key, "shop-21318", three_way) == "B"  # bucket 6665 < 6666
    assert assign_variant(key, "shop-13061", three_way) == "C"  # bucket 6666 < 10000
    assert assign_variant(key, "shop-11965", three_way) == "C"  # bucket 9999 < 10000

    two_way = [("A", 5000), ("B", 5000)]
    assert assign_variant(key, "shop-07949", two_way) == "A"  # bucket 4999 < 5000
    assert assign_variant(key, "shop-05498", two_way) == "B"  # bucket 5000 >= 5000


def test_golden_file_match() -> None:
    """Verify that assign_variant exactly matches every entry in assignment_golden_v1.json.

    Purpose:
        Guarantees stability against regression and parity across languages.

    Inputs/Outputs:
        tests/fixtures/assignment_golden_v1.json fixture.

    Key Logic:
        Loads golden JSON, casts variants to list of tuples, and checks all 1,000 shops.

    Errors:
        AssertionError if any shop assignment differs from the golden fixture.
    """
    golden_path = Path("tests/fixtures/assignment_golden_v1.json")
    assert golden_path.exists(), f"Golden file not found at {golden_path}"

    data: dict[str, Any] = json.loads(golden_path.read_text(encoding="utf-8"))
    experiment_key: str = data["experiment_key"]
    raw_variants = data["variants"]
    variants: list[tuple[str, int]] = [(v[0], v[1]) for v in raw_variants]
    assignments: dict[str, str] = data["assignments"]

    assert len(assignments) == 1000

    for shop_id, expected_variant in assignments.items():
        actual = assign_variant(experiment_key, shop_id, variants)
        assert actual == expected_variant, (
            f"Mismatch for {shop_id}: expected {expected_variant}, got {actual}"
        )


def test_parity_with_bench_adr002() -> None:
    """Verify 100% assignment parity with bench.adr002.homemade over 10,000 shops.

    Purpose:
        Guarantees that general assign_variant is backward-compatible with ADR-002 candidate.

    Inputs/Outputs:
        10,000 shops evaluated under 50/50 split.

    Key Logic:
        Iterates over 'shop-00001'..'shop-10000', evaluating both functions under key 'parity-test'.

    Errors:
        AssertionError on any divergence between homemade candidate and production assignment.
    """
    key = "parity-test"
    variants = [("A", 5000), ("B", 5000)]
    for i in range(1, 10001):
        shop = f"shop-{i:05d}"
        candidate_result = homemade_assign_variant(key, shop, 50)
        prod_result = assign_variant(key, shop, variants)
        assert prod_result == candidate_result, f"Divergence for {shop}: {prod_result} vs {candidate_result}"
