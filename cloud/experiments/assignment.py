"""Deterministic experiment assignment engine using SHA-256 bucketing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def compute_bucket(experiment_key: str, shop_id: str) -> int:
    """Compute a deterministic bucket in [0, 9999] for an experiment and shop.

    Purpose:
        Hashes the experiment key and shop ID into a deterministic integer
        bucket from 0 to 9999 (10,000 basis points) using the first 8 bytes
        of SHA-256.

    Inputs/Outputs:
        experiment_key: Unique identifier string for the experiment.
        shop_id: Unique identifier string for the shop.
        Returns: Integer in range [0, 9999].

    Key Logic:
        Validates non-empty string arguments.
        Encodes '{experiment_key}:{shop_id}' as UTF-8, hashes with SHA-256,
        slices the first 8 bytes, interprets them as big-endian uint64, and
        takes modulo 10000.

    Errors:
        ValueError: If experiment_key or shop_id is not a non-empty string.
    """
    if not isinstance(experiment_key, str) or not experiment_key:
        raise ValueError("experiment_key must be a non-empty string")
    if not isinstance(shop_id, str) or not shop_id:
        raise ValueError("shop_id must be a non-empty string")

    # Hash combining key and shop id into an 8-byte big-endian slice.
    digest_bytes = hashlib.sha256(f"{experiment_key}:{shop_id}".encode()).digest()[:8]
    return int.from_bytes(digest_bytes, "big") % 10000


def assign_variant(
    experiment_key: str,
    shop_id: str,
    variants: list[tuple[str, int]],
) -> str:
    """Assign a shop to an experiment variant based on basis-point weights.

    Purpose:
        Deterministically allocates a shop to one of the provided variants
        proportional to its basis-point weight (summing to 10,000).

    Inputs/Outputs:
        experiment_key: Unique identifier string for the experiment.
        shop_id: Unique identifier string for the shop.
        variants: List of (variant_name, weight_basis_points) tuples.
        Returns: Selected variant name string.

    Key Logic:
        Validates inputs, unique variant names, non-negative integer weights,
        and that weights sum to exactly 10,000 basis points.
        Computes bucket via compute_bucket, walks cumulative weights, and
        selects the first variant where bucket < cumulative_weight.

    Errors:
        ValueError: On empty inputs, invalid types, duplicate names, negative weights,
            or weights sum != 10000.
    """
    if not isinstance(variants, list) or not variants:
        raise ValueError("variants must be a non-empty list of (name, weight) tuples")

    seen_names: set[str] = set()
    total_weight = 0
    for item in variants:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError(f"Each variant must be a 2-tuple (name, weight), got {item!r}")
        name, weight = item
        if not isinstance(name, str) or not name:
            raise ValueError(f"Variant name must be a non-empty string, got {name!r}")
        if name in seen_names:
            raise ValueError(f"Duplicate variant name: {name!r}")
        seen_names.add(name)
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 0:
            raise ValueError(f"Variant weight must be a non-negative integer, got {weight!r}")
        total_weight += weight

    if total_weight != 10000:
        raise ValueError(f"Variant weights must sum to exactly 10000 basis points, got {total_weight}")

    bucket = compute_bucket(experiment_key, shop_id)
    cumulative = 0
    for name, weight in variants:
        cumulative += weight
        # Variant with weight 0 leaves cumulative unchanged, so bucket < cumulative cannot trigger.
        if bucket < cumulative:
            return name

    raise ValueError(f"Could not assign variant for bucket {bucket} with cumulative {cumulative}")


def write_golden_file(path: Path | str) -> None:
    """Generate the v1 golden assignments file for cross-language validation.

    Purpose:
        Produces a stable JSON fixture mapping 1,000 sequential shops to
        assigned variants under a standard three-way split.

    Inputs/Outputs:
        path: File path where the golden JSON fixture should be saved.

    Key Logic:
        Generates shop IDs 'shop-00001'..'shop-01000', runs assign_variant
        with experiment_key='golden-v1' and variants [('A', 3333), ('B', 3333), ('C', 3334)],
        and writes the output as formatted JSON.

    Errors:
        ValueError: If path is invalid or cannot be written.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    experiment_key = "golden-v1"
    variants: list[tuple[str, int]] = [("A", 3333), ("B", 3333), ("C", 3334)]
    shop_ids = [f"shop-{i:05d}" for i in range(1, 1001)]

    assignments = {shop_id: assign_variant(experiment_key, shop_id, variants) for shop_id in shop_ids}

    data = {
        "experiment_key": experiment_key,
        "variants": variants,
        "assignments": assignments,
    }

    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "--write-golden":
        write_golden_file(sys.argv[2])
    else:
        sys.stderr.write("Usage: python -m cloud.experiments.assignment --write-golden <path>\n")
        sys.exit(1)
