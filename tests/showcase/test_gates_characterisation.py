# TERM: characterisation test - a test that pins existing behaviour as a safety net before refactoring.
# TERM: gate tuple - a four-element tuple (gate_id, passed, status, detail) representing one gate verdict.
# TERM: drift - a freshly rendered file differs from the committed one in docs/.
"""Characterisation tests for compute_gates and exit_code_for, pinning exact gate outcomes."""

from __future__ import annotations

import pytest

from showcase.phase0 import (
    FAIL,
    NOT_RUN,
    PASS,
    CheckResult,
    Drift,
    Gate,
    compute_gates,
    exit_code_for,
)

pytestmark = pytest.mark.unit


def make_check(
    check_id: str,
    exit_code: int | None = 0,
    status: str = PASS,
    drifts: list[Drift] | None = None,
    numbers: dict[str, str] | None = None,
    notes: list[str] | None = None,
) -> CheckResult:
    """WHAT: Builds a CheckResult with the given parameters for testing compute_gates.
    WHY: Characterisation tests construct specific CheckResult inputs to exercise gate logic.

    Args:
        check_id: which check.
        exit_code: exit code (None for did not finish).
        status: PASS, FAIL or NOT RUN.
        drifts: drifted files.
        numbers: numbers dictionary.
        notes: notes list.
    Returns:
        A CheckResult.
    Example:
        make_check("suite") -> a passing CheckResult
    """
    return CheckResult(
        check_id=check_id,
        title=f"title {check_id}",
        claim=f"claim {check_id}",
        command=f"cmd {check_id}",
        exit_code=exit_code,
        status=status,
        output="output",
        numbers=numbers if numbers is not None else {},
        notes=notes if notes is not None else [],
        drifts=drifts if drifts is not None else [],
        seconds=1.0,
    )


def passing_checks() -> dict[str, CheckResult]:
    """WHAT: Returns a dictionary of all checks in an all-passing state.
    WHY: Baseline input for gate characterisation cases.

    Args:
        none.
    Returns:
        Dictionary of check results keyed by check id.
    Example:
        passing_checks()["suite"].status -> "PASS"
    """
    return {
        "suite": make_check("suite"),
        "feature_truth": make_check("feature_truth"),
        "data_provenance": make_check("data_provenance"),
        "screen_inventory": make_check("screen_inventory"),
        "adr_000": make_check("adr_000"),
        "scorecard_flaws": make_check(
            "scorecard_flaws",
            numbers={
                "flaws failed with AssertionError": "8",
                "positive control passed": "yes",
            },
        ),
        "v1_rerun": make_check("v1_rerun"),
    }


def gate_tuples(gates: list[Gate]) -> list[tuple[str, bool, str, str]]:
    """WHAT: Extracts (gate_id, passed, status, detail) tuples from a list of Gate objects.
    WHY: Allows concise literal assertions against gate outcomes.

    Args:
        gates: list of Gate objects.
    Returns:
        List of (gate_id, passed, status, detail) tuples.
    Example:
        gate_tuples([Gate("G1", "c", True, "PASS", "d")]) -> [("G1", True, "PASS", "d")]
    """
    return [(gate.gate_id, gate.passed, gate.status, gate.detail) for gate in gates]


def test_case_a_all_pass_no_drift() -> None:
    """WHAT: Tests gate computation when every check passes with exit code 0 and no drift.
    WHY: Pins the baseline all-passing gate behaviour and 0 exit code.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        compute_gates(passing_checks()) -> 6 passing gates, exit code 0
    """
    checks = passing_checks()
    gates = compute_gates(checks)
    expected = [
        ("G1", True, "PASS", "feature_truth exit 0"),
        ("G2", True, "PASS", "data_provenance exit 0"),
        ("G3", True, "PASS", "flaws failed with AssertionError: 8; positive control passed: yes"),
        ("G4", True, "PASS", "adr_000 exit 0"),
        ("G5", True, "PASS", "see the v1 rerun section"),
        ("G6", True, "PASS", "suite PASS, screen_inventory exit 0, drifted files: 0"),
    ]
    assert gate_tuples(gates) == expected
    assert exit_code_for(gates) == 0


def test_case_b_feature_truth_exits_one() -> None:
    """WHAT: Tests gate computation when feature_truth exits 1 and others pass.
    WHY: Pins that G1 fails and overall exit code is 1.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        feature_truth exits 1 -> G1 fails, exit code 1
    """
    checks = passing_checks()
    checks["feature_truth"] = make_check("feature_truth", exit_code=1, status=FAIL)
    gates = compute_gates(checks)
    expected = [
        ("G1", False, "FAIL", "feature_truth exit 1"),
        ("G2", True, "PASS", "data_provenance exit 0"),
        ("G3", True, "PASS", "flaws failed with AssertionError: 8; positive control passed: yes"),
        ("G4", True, "PASS", "adr_000 exit 0"),
        ("G5", True, "PASS", "see the v1 rerun section"),
        ("G6", True, "PASS", "suite PASS, screen_inventory exit 0, drifted files: 0"),
    ]
    assert gate_tuples(gates) == expected
    assert exit_code_for(gates) == 1


def test_case_b_data_provenance_exits_one() -> None:
    """WHAT: Tests gate computation when data_provenance exits 1 and others pass.
    WHY: Pins that G2 fails and overall exit code is 1.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        data_provenance exits 1 -> G2 fails, exit code 1
    """
    checks = passing_checks()
    checks["data_provenance"] = make_check("data_provenance", exit_code=1, status=FAIL)
    gates = compute_gates(checks)
    expected = [
        ("G1", True, "PASS", "feature_truth exit 0"),
        ("G2", False, "FAIL", "data_provenance exit 1"),
        ("G3", True, "PASS", "flaws failed with AssertionError: 8; positive control passed: yes"),
        ("G4", True, "PASS", "adr_000 exit 0"),
        ("G5", True, "PASS", "see the v1 rerun section"),
        ("G6", True, "PASS", "suite PASS, screen_inventory exit 0, drifted files: 0"),
    ]
    assert gate_tuples(gates) == expected
    assert exit_code_for(gates) == 1


def test_case_b_screen_inventory_exits_one() -> None:
    """WHAT: Tests gate computation when screen_inventory exits 1 and others pass.
    WHY: Pins that G6 fails and overall exit code is 1.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        screen_inventory exits 1 -> G6 fails, exit code 1
    """
    checks = passing_checks()
    checks["screen_inventory"] = make_check("screen_inventory", exit_code=1, status=FAIL)
    gates = compute_gates(checks)
    expected = [
        ("G1", True, "PASS", "feature_truth exit 0"),
        ("G2", True, "PASS", "data_provenance exit 0"),
        ("G3", True, "PASS", "flaws failed with AssertionError: 8; positive control passed: yes"),
        ("G4", True, "PASS", "adr_000 exit 0"),
        ("G5", True, "PASS", "see the v1 rerun section"),
        ("G6", False, "FAIL", "suite PASS, screen_inventory exit 1, drifted files: 0"),
    ]
    assert gate_tuples(gates) == expected
    assert exit_code_for(gates) == 1


def test_case_b_adr_000_exits_one() -> None:
    """WHAT: Tests gate computation when adr_000 exits 1 and others pass.
    WHY: Pins that G4 fails and overall exit code is 1.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        adr_000 exits 1 -> G4 fails, exit code 1
    """
    checks = passing_checks()
    checks["adr_000"] = make_check("adr_000", exit_code=1, status=FAIL)
    gates = compute_gates(checks)
    expected = [
        ("G1", True, "PASS", "feature_truth exit 0"),
        ("G2", True, "PASS", "data_provenance exit 0"),
        ("G3", True, "PASS", "flaws failed with AssertionError: 8; positive control passed: yes"),
        ("G4", False, "FAIL", "adr_000 exit 1"),
        ("G5", True, "PASS", "see the v1 rerun section"),
        ("G6", True, "PASS", "suite PASS, screen_inventory exit 0, drifted files: 0"),
    ]
    assert gate_tuples(gates) == expected
    assert exit_code_for(gates) == 1


def test_case_c_scorecard_flaws_fail() -> None:
    """WHAT: Tests gate computation when scorecard_flaws fails.
    WHY: Pins that G3 fails and overall exit code is 1.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        scorecard_flaws FAIL -> G3 fails, exit code 1
    """
    checks = passing_checks()
    checks["scorecard_flaws"] = make_check(
        "scorecard_flaws",
        exit_code=1,
        status=FAIL,
        numbers={
            "flaws failed with AssertionError": "7",
            "positive control passed": "yes",
        },
    )
    gates = compute_gates(checks)
    expected = [
        ("G1", True, "PASS", "feature_truth exit 0"),
        ("G2", True, "PASS", "data_provenance exit 0"),
        ("G3", False, "FAIL", "flaws failed with AssertionError: 7; positive control passed: yes"),
        ("G4", True, "PASS", "adr_000 exit 0"),
        ("G5", True, "PASS", "see the v1 rerun section"),
        ("G6", True, "PASS", "suite PASS, screen_inventory exit 0, drifted files: 0"),
    ]
    assert gate_tuples(gates) == expected
    assert exit_code_for(gates) == 1


def test_case_d_v1_rerun_fail() -> None:
    """WHAT: Tests gate computation when v1_rerun fails.
    WHY: Pins that G5 fails with FAIL status and overall exit code is 1.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        v1_rerun FAIL -> G5 fails, exit code 1
    """
    checks = passing_checks()
    checks["v1_rerun"] = make_check(
        "v1_rerun",
        exit_code=1,
        status=FAIL,
        notes=["the rerun did not produce results"],
    )
    gates = compute_gates(checks)
    expected = [
        ("G1", True, "PASS", "feature_truth exit 0"),
        ("G2", True, "PASS", "data_provenance exit 0"),
        ("G3", True, "PASS", "flaws failed with AssertionError: 8; positive control passed: yes"),
        ("G4", True, "PASS", "adr_000 exit 0"),
        ("G5", False, "FAIL", "the rerun did not produce results"),
        ("G6", True, "PASS", "suite PASS, screen_inventory exit 0, drifted files: 0"),
    ]
    assert gate_tuples(gates) == expected
    assert exit_code_for(gates) == 1


def test_case_d_v1_rerun_not_run() -> None:
    """WHAT: Tests gate computation when v1_rerun is NOT RUN.
    WHY: Pins that G5 fails with NOT RUN status and overall exit code is 1.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        v1_rerun NOT RUN -> G5 fails, exit code 1
    """
    checks = passing_checks()
    checks["v1_rerun"] = make_check(
        "v1_rerun",
        exit_code=None,
        status=NOT_RUN,
        notes=["Docker is not running"],
    )
    gates = compute_gates(checks)
    expected = [
        ("G1", True, "PASS", "feature_truth exit 0"),
        ("G2", True, "PASS", "data_provenance exit 0"),
        ("G3", True, "PASS", "flaws failed with AssertionError: 8; positive control passed: yes"),
        ("G4", True, "PASS", "adr_000 exit 0"),
        ("G5", False, "NOT RUN", "Docker is not running"),
        ("G6", True, "PASS", "suite PASS, screen_inventory exit 0, drifted files: 0"),
    ]
    assert gate_tuples(gates) == expected
    assert exit_code_for(gates) == 1


def test_case_e_suite_fail() -> None:
    """WHAT: Tests gate computation when suite fails.
    WHY: Pins that G6 fails and overall exit code is 1.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        suite FAIL -> G6 fails, exit code 1
    """
    checks = passing_checks()
    checks["suite"] = make_check("suite", exit_code=1, status=FAIL)
    gates = compute_gates(checks)
    expected = [
        ("G1", True, "PASS", "feature_truth exit 0"),
        ("G2", True, "PASS", "data_provenance exit 0"),
        ("G3", True, "PASS", "flaws failed with AssertionError: 8; positive control passed: yes"),
        ("G4", True, "PASS", "adr_000 exit 0"),
        ("G5", True, "PASS", "see the v1 rerun section"),
        ("G6", False, "FAIL", "suite FAIL, screen_inventory exit 0, drifted files: 0"),
    ]
    assert gate_tuples(gates) == expected
    assert exit_code_for(gates) == 1


def test_case_f_checker_drifted_file() -> None:
    """WHAT: Tests gate computation when one checker has a drifted file and exits 0.
    WHY: Pins that G6 fails when a file drifted even if exit code is 0.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        feature_truth drifted -> G6 fails, exit code 1
    """
    checks = passing_checks()
    checks["feature_truth"] = make_check(
        "feature_truth",
        exit_code=0,
        status=PASS,
        drifts=[Drift("feature-truth.md", "docs/feature-truth.md", [("+new", "content changed")])],
    )
    gates = compute_gates(checks)
    expected = [
        ("G1", True, "PASS", "feature_truth exit 0"),
        ("G2", True, "PASS", "data_provenance exit 0"),
        ("G3", True, "PASS", "flaws failed with AssertionError: 8; positive control passed: yes"),
        ("G4", True, "PASS", "adr_000 exit 0"),
        ("G5", True, "PASS", "see the v1 rerun section"),
        ("G6", False, "FAIL", "suite PASS, screen_inventory exit 0, drifted files: 1"),
    ]
    assert gate_tuples(gates) == expected
    assert exit_code_for(gates) == 1
