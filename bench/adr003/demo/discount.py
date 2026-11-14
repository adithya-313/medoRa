"""Discount calculation used by the ADR-003 weakened-assertion demo."""

from __future__ import annotations


def apply_discount(price: float, percent: int) -> float:
    """Apply a percentage discount to a price, rounded to 2 decimals.

    Purpose:
        Validates price and percent, computes price * (1 - percent / 100),
        and rounds the result to 2 decimal places.

    Inputs/Outputs:
        price: Non-negative price.
        percent: Discount percentage in the inclusive range 0..100.
        Returns: Discounted price rounded to 2 decimal places.

    Errors:
        ValueError: If price < 0 or percent is outside 0..100.
    """
    if price < 0:
        raise ValueError("price must be >= 0")
    if percent < 0 or percent > 100:
        raise ValueError("percent must be in the range 0..100")

    result = price * (1.0 - percent / 100.0)
    return round(result, 2)
