# TERM: unit test - an automated check that tests isolated logic without external dependencies.
# TERM: test fixture - synthetic data structured to exercise specific code execution paths.
"""Unit tests for ADR-001 dependency manager benchmark harness."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import pytest

from bench.adr001.run_benchmark import (
    check_tool_hard_rules,
    compute_median,
    evaluate_decision,
    summarize_tool_runs,
    verify_h1_reproducible,
    verify_h2_hashes,
)


def test_compute_median_odd_and_even() -> None:
    """WHAT: Verifies median computation for odd, even, and singleton lists of floats.
    WHY: Required by ADR-001 specification to ensure statistical correctness of scores.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        test_compute_median_odd_and_even() -> None
    """
    assert compute_median([3.0, 1.0, 2.0]) == 2.0
    assert compute_median([1.0, 4.0, 3.0, 2.0]) == 2.5
    assert compute_median([42.0]) == 42.0
    with pytest.raises(ValueError, match="Cannot compute median"):
        compute_median([])


def test_h2_fails_when_lock_missing_hash() -> None:
    """WHAT: Verifies that verify_h2_hashes rejects lock files missing package hashes.
    WHY: Implements hard rule H2 enforcement preventing unpinned/unverified dependencies.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        test_h2_fails_when_lock_missing_hash() -> None
    """
    uv_fake = '[[package]]\nname = "foo"\nversion = "1.0"\n'
    passed_uv, _ = verify_h2_hashes("uv", uv_fake)
    assert not passed_uv
    poetry_fake = '[[package]]\nname = "foo"\nfiles = []\n'
    passed_poetry, _ = verify_h2_hashes("poetry", poetry_fake)
    assert not passed_poetry
    pip_fake = "foo==1.0\n"
    passed_pip, _ = verify_h2_hashes("pip-tools", pip_fake)
    assert not passed_pip


def test_h1_fails_when_lock_hashes_differ() -> None:
    """WHAT: Verifies that verify_h1_reproducible fails when lock SHA-256 digests differ.
    WHY: Implements hard rule H1 enforcement guaranteeing deterministic lock generation.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        test_h1_fails_when_lock_hashes_differ() -> None
    """
    passed_match, _ = verify_h1_reproducible("abc123sha", "abc123sha")
    assert passed_match
    passed_mismatch, detail = verify_h1_reproducible("abc123sha", "def456sha")
    assert not passed_mismatch
    assert "Disqualified" in detail


def build_candidate_fixture(
    tool_name: str,
    h1_pass: bool,
    warm_s: float,
    cold_s: float,
) -> dict[str, Any]:
    """WHAT: Constructs a synthetic candidate record for testing decision rules.
    WHY: Modular helper keeping individual test functions under 30 real code lines.

    Args:
        tool_name: Name of tool.
        h1_pass: Whether H1 passed.
        warm_s: Median warm install seconds.
        cold_s: Median cold install seconds.
    Returns:
        Dictionary fixture representing candidate results.
    Example:
        build_candidate_fixture("toolA", True, 2.0, 10.0) -> {...}
    """
    return {
        "hard_rules": {
            "H1": {"passed": h1_pass},
            "H2": {"passed": True},
            "H3": {"passed": True},
            "H4": {"passed": True},
        },
        "summary": {
            "median_warm_install_seconds": warm_s,
            "median_cold_install_seconds": cold_s,
            "median_lock_seconds": 1.0,
        },
    }


def test_decision_picks_fastest_warm_installer() -> None:
    """WHAT: Verifies decision rule chooses tool with lowest warm install time.
    WHY: Implements primary scored criterion in ADR-001.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        test_decision_picks_fastest_warm_installer() -> None
    """
    results: dict[str, Any] = {
        "tools": {
            "fast_tool": build_candidate_fixture("fast_tool", True, 2.0, 15.0),
            "slow_tool": build_candidate_fixture("slow_tool", True, 5.0, 10.0),
        }
    }
    winner, rationale = evaluate_decision(results)
    assert winner == "fast_tool"
    assert "lowest median warm install time" in rationale


def test_disqualified_tool_cannot_win() -> None:
    """WHAT: Verifies that a tool failing any hard rule (H1) cannot win.
    WHY: Ensures hard disqualification rules take absolute precedence over timings.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        test_disqualified_tool_cannot_win() -> None
    """
    results: dict[str, Any] = {
        "tools": {
            "fast_but_bad": build_candidate_fixture("fast_but_bad", False, 1.0, 5.0),
            "slower_clean": build_candidate_fixture("slower_clean", True, 4.0, 8.0),
        }
    }
    winner, _ = evaluate_decision(results)
    assert winner == "slower_clean"


def test_tie_break_picks_faster_cold_install() -> None:
    """WHAT: Verifies 10% warm install tie breaks in favour of lower cold install.
    WHY: Validates the secondary tie-break scoring policy of ADR-001.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        test_tie_break_picks_faster_cold_install() -> None
    """
    results: dict[str, Any] = {
        "tools": {
            "tool_a": build_candidate_fixture("tool_a", True, 10.0, 30.0),
            "tool_b": build_candidate_fixture("tool_b", True, 10.5, 18.0),
        }
    }
    winner, rationale = evaluate_decision(results)
    assert winner == "tool_b"
    assert "tie-break" in rationale


def test_failed_step_is_reported_not_dropped() -> None:
    """WHAT: Verifies that failed execution steps are captured and cause H3 to fail.
    WHY: Ensures failures are transparently surfaced rather than silently dropped.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        test_failed_step_is_reported_not_dropped() -> None
    """
    failed_runs = [
        {
            "lock": {"seconds": 1.0, "exit_code": 0, "status": "ok", "tail": ""},
            "cold_install": {"seconds": 2.0, "exit_code": 1, "status": "failed", "tail": "Error 404"},
            "warm_install": {"seconds": 0.5, "exit_code": 0, "status": "ok", "tail": ""},
        }
    ]
    summary = summarize_tool_runs("tool_x", failed_runs)
    assert summary["median_cold_install_seconds"] == 0.0
    rules = check_tool_hard_rules("pip", Path("."), Path("."), failed_runs)
    assert not rules["H3"]["passed"]
    assert "One or more runs failed" in rules["H3"]["detail"]
