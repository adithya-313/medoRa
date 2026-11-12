"""Homemade feature flag and variant assignment candidate for ADR-002."""

from __future__ import annotations

import hashlib


def assign_variant(experiment_key: str, shop_id: str, split_percent: int) -> str:
    """Deterministically assign a shop to experiment variant A or B.

    Computes SHA-256 of '{experiment_key}:{shop_id}', takes the first 8 bytes as a
    big-endian integer modulo 10000 to produce a bucket in [0, 9999]. If bucket < split_percent * 100,
    assigns 'A', otherwise 'B'.

    Args:
        experiment_key: Unique identifier of the experiment.
        shop_id: Unique shop identifier.
        split_percent: Percentage allocated to variant A (0 to 100).

    Returns:
        'A' if bucket falls within split percentage, else 'B'.

    Raises:
        ValueError: If split_percent is not between 0 and 100 inclusive.
    """
    if split_percent < 0 or split_percent > 100:
        raise ValueError(f"split_percent must be in range 0..100, got {split_percent}")

    digest_bytes = hashlib.sha256(f"{experiment_key}:{shop_id}".encode()).digest()[:8]
    bucket = int.from_bytes(digest_bytes, "big") % 10000
    return "A" if bucket < split_percent * 100 else "B"
