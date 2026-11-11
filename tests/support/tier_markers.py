"""Pytest plugin enforcing exactly one tier marker per test item."""

import pytest

TIERS: dict[str, str] = {
    "unit": "no Docker, network or services (local temp files and local git allowed)",
    "integration": "needs real services started in Docker",
    "e2e": "full user flow across apps",
    "chaos": "fault injection",
    "bench": "timing measurement, numbers valid only on the owner's laptop",
}


def pytest_configure(config: pytest.Config) -> None:
    """Register test tier markers in pytest configuration.

    Purpose:
        Registers each supported test tier as a formal pytest marker so that
        --strict-markers does not reject them.

    Inputs/Outputs:
        config: Pytest configuration instance. Returns None.

    Key Logic:
        Iterates over the TIERS dictionary and adds marker definitions to config.

    Errors:
        None.
    """
    for tier, description in TIERS.items():
        config.addinivalue_line("markers", f"{tier}: {description}")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Validate that every collected test item has exactly one tier marker.

    Purpose:
        Enforces test tier taxonomy across the entire test suite.

    Inputs/Outputs:
        config: Pytest configuration instance.
        items: List of collected pytest test items. Returns None.

    Key Logic:
        Runs tryfirst=True to see all collected tests prior to -m filtering.
        Counts how many markers belonging to TIERS each item possesses.
        Collects errors for any item that has 0 or more than 1 tier marker.
        Raises pytest.UsageError if any violations are detected.

    Errors:
        pytest.UsageError: Raised when one or more test items lack exactly one tier marker.
    """
    errors: list[str] = []
    for item in items:
        tier_markers = [marker for marker in item.iter_markers() if marker.name in TIERS]
        count = len(tier_markers)
        if count != 1:
            errors.append(f"{item.nodeid}: has {count} tier markers (needs exactly 1)")

    if len(errors) > 0:
        raise pytest.UsageError("\n".join(errors))
