"""Unit tests for apply_discount with full assertion coverage (ADR-003 demo)."""

from __future__ import annotations

import pytest

from bench.adr003.demo.discount import apply_discount

pytestmark = pytest.mark.unit


def test_apply_discount_standard_values() -> None:
    """Verify correct discounted values for typical inputs."""
    assert apply_discount(100.0, 0) == 100.0
    assert apply_discount(100.0, 50) == 50.0
    assert apply_discount(100.0, 25) == 75.0


def test_apply_discount_boundaries() -> None:
    """Verify both boundaries: percent 0 and percent 100."""
    assert apply_discount(0.0, 0) == 0.0
    assert apply_discount(0.0, 100) == 0.0
    assert apply_discount(50.0, 100) == 0.0


def test_apply_discount_rounding() -> None:
    """Verify rounding to 2 decimal places."""
    assert apply_discount(100.0, 33) == 67.0


def test_apply_discount_negative_price_raises() -> None:
    """Verify negative price raises ValueError."""
    with pytest.raises(ValueError, match="price"):
        apply_discount(-1.0, 10)


def test_apply_discount_percent_below_zero_raises() -> None:
    """Verify percent below 0 raises ValueError."""
    with pytest.raises(ValueError, match="percent"):
        apply_discount(100.0, -1)


def test_apply_discount_percent_above_hundred_raises() -> None:
    """Verify percent above 100 raises ValueError."""
    with pytest.raises(ValueError, match="percent"):
        apply_discount(100.0, 101)
