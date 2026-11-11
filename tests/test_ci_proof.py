"""Proof test that deliberately fails to verify the CI ruleset blocks merging a red PR."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_deliberate_failure_for_ruleset_proof() -> None:
    """Deliberately failing test asserting 1 == 2 to prove the ruleset blocks a red PR."""
    actual: int = 1
    assert actual == 2
