# TERM: pip freeze - a pip command that prints every installed package as name==version.
# TERM: normalised name - a package name lower-cased with '-', '_' and '.' treated as the same character.
"""Unit tests for the package-list comparison helpers. No Docker, no network."""

from __future__ import annotations

import pytest

from tools.phase0.run_v1_suite_docker import (
    compare_packages,
    normalise_name,
    parse_freeze,
    parse_installed_from_log,
)

pytestmark = pytest.mark.unit


def test_normalise_name_unifies_spellings() -> None:
    """WHAT: Checks that different spellings of one package normalise to the same text.
    WHY: Otherwise "Typing_Extensions" and "typing-extensions" would look like different packages.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        "Typing_Extensions" and "typing.extensions" -> both "typing-extensions"
    """
    assert normalise_name("Typing_Extensions") == "typing-extensions"
    assert normalise_name("typing.extensions") == "typing-extensions"


def test_parse_freeze_reads_name_and_version() -> None:
    """WHAT: Checks parse_freeze reads name==version lines and ignores other lines.
    WHY: The freeze file is the pinned record of the image's packages.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        "torch==2.12.0" and "-e git+x" -> {"torch": "2.12.0"}
    """
    assert parse_freeze("torch==2.12.0\n-e git+x\n") == {"torch": "2.12.0"}


def test_parse_installed_from_log_splits_on_last_dash() -> None:
    """WHAT: Checks that "Successfully installed" tokens are split into name and version correctly,
    including names that contain dashes.
    WHY: The earlier run's package list can only be recovered from its pip log.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        "Successfully installed nvidia-cufile-1.2.3 numpy-2.1.0" -> {"nvidia-cufile": "1.2.3", "numpy": "2.1.0"}
    """
    log_text = "noise\nSuccessfully installed nvidia-cufile-1.2.3 numpy-2.1.0\n"
    assert parse_installed_from_log(log_text) == {"nvidia-cufile": "1.2.3", "numpy": "2.1.0"}


def test_compare_packages_reports_every_difference() -> None:
    """WHAT: Checks compare_packages lists extra packages on each side and version changes.
    WHY: The comparison must never hide a difference between the two environments.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        image {a:1, pip:24, c:3}, earlier {a:2, c:3, d:4} -> only_in_image ["pip==24"], only_in_earlier_run ["d==4"], differences [["a","1","2"]]
    """
    image = {"a": "1", "pip": "24", "c": "3"}
    earlier = {"a": "2", "c": "3", "d": "4"}
    difference = compare_packages(image, earlier)
    assert difference["only_in_image"] == ["pip==24"]
    assert difference["only_in_earlier_run"] == ["d==4"]
    assert difference["version_differences"] == [["a", "1", "2"]]
