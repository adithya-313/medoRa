# TERM: pip log - the text pip prints while installing packages, including each file's size.
# TERM: wheel - a pre-built Python package file that pip downloads.
"""Unit tests for parse_pip_sizes. No network, no Docker."""

from __future__ import annotations

import pytest

from tools.phase0.run_v1_suite_docker import parse_pip_sizes

pytestmark = pytest.mark.unit


def test_downloaded_and_cached_sizes_are_separated() -> None:
    """WHAT: Checks that "Downloading" lines and "Using cached" lines are added up separately.
    WHY: The report must show how much was really downloaded versus served from the cache.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        1.5 MB + 500 kB downloaded, 2 GB cached -> (2000000, 2000000000)
    """
    log_text = (
        "Downloading a-1.0.whl (1.5 MB)\n"
        "Downloading b-1.0.whl (500 kB)\n"
        "Using cached nvidia_c-1.0.whl (2.0 GB)\n"
    )
    assert parse_pip_sizes(log_text) == (2_000_000, 2_000_000_000)


def test_log_without_sizes_gives_zero() -> None:
    """WHAT: Checks that a log with no size lines gives (0, 0).
    WHY: A failed early install must not invent numbers.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        "ERROR: no matching distribution" -> (0, 0)
    """
    assert parse_pip_sizes("ERROR: no matching distribution\n") == (0, 0)
