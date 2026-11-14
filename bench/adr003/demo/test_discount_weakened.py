"""Unit tests for apply_discount with weakened assertion coverage (ADR-003 demo, H4).

This file is deliberately weak: the boundary and ValueError assertions are
removed so that fewer mutants are killed. Used only for the ADR-003 H4 demo.
"""

from __future__ import annotations

import pytest

from bench.adr003.demo.discount import apply_discount

pytestmark = pytest.mark.unit


def test_apply_discount_standard_values() -> None:
    """Verify correct discounted values for typical inputs."""
    assert apply_discount(100.0, 0) == 100.0
    assert apply_discount(100.0, 50) == 50.0
    assert apply_discount(100.0, 25) == 75.0


def test_apply_discount_rounding() -> None:
    """Verify rounding to 2 decimal places."""
    assert apply_discount(100.0, 33) == 67.0
