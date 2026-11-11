# TERM: flaw test - a test that states CORRECT behaviour and is expected to FAIL on v1.
# TERM: positive control - a test that must PASS on v1.
# TERM: junit fixture - a small junit XML file written into pytest's temp folder for one test.
"""Unit tests for the verification logic of tools/phase0/run_scorecard_flaws.py. No Docker, no network."""

from __future__ import annotations

from pathlib import Path

from tools.phase0.run_scorecard_flaws import FLAWS, flaw_id_of, render_results, verify_rows
from tools.phase0.run_v1_suite import TestRow, parse_junit

FLAW_NAMES = {
    "f1": "test_f1_no_orders_is_not_trusted",
    "f2": "test_f2_score_reflects_failure_rate_not_failure_count",
    "f3": "test_f3_pending_orders_not_yet_due_are_not_failures",
    "f4": "test_f4_scorecard_does_not_mix_tenants",
    "f5": "test_f5_money_is_exact_decimal",
    "f6": "test_f6_old_failures_age_out",
    "f7": "test_f7_loss_is_never_negative",
    "f8": "test_f8_success_rate_is_not_truncated",
}
CONTROL = "test_positive_control"


def good_results() -> list[tuple[str, str, str]]:
    """WHAT: Returns the expected result of a healthy run: eight flaw tests failing with an AssertionError
    and the positive control passing, as (test name, outcome, message) triples.
    WHY: Each negative test below changes exactly one entry of this list.

    Args:
        none.
    Returns:
        A list of 9 (name, outcome, message) triples.
    Example:
        good_results()[0] -> ("test_f1_no_orders_is_not_trusted", "failed", "AssertionError: v1 returned ...")
    """
    results = [
        (name, "failed", f"AssertionError: v1 returned a wrong value for {flaw_id}")
        for flaw_id, name in FLAW_NAMES.items()
    ]
    results.append((CONTROL, "passed", ""))
    return results


def replace_result(
    results: list[tuple[str, str, str]], name: str, outcome: str, message: str
) -> list[tuple[str, str, str]]:
    """WHAT: Returns a copy of the results where the entry for `name` has a new outcome and message.
    WHY: Each negative test changes exactly one result and keeps the rest healthy.

    Args:
        results: (name, outcome, message) triples.
        name: the test name to change.
        outcome: the new outcome.
        message: the new message.
    Returns:
        The changed copy.
    Example:
        replace_result(good_results(), "test_f2_...", "passed", "") -> same list with f2 passed
    """
    changed = []
    for entry in results:
        changed.append((name, outcome, message) if entry[0] == name else entry)
    return changed


def parse_results(tmp_path: Path, results: list[tuple[str, str, str]]) -> list[TestRow]:
    """WHAT: Writes the given results as a small junit XML file (with correct totals) and parses it with the
    real parser, which also reconciles the totals.
    WHY: The verification tests should use the same parse step as the real runner.

    Args:
        tmp_path: pytest's temporary folder.
        results: (test name, outcome in passed/failed/error, message) triples.
    Returns:
        The parsed rows.
    Example:
        [("test_a", "failed", "AssertionError: x")] -> one TestRow with outcome "failed"
    """
    children = {
        "passed": "",
        "failed": '<failure message="{m}">trace</failure>',
        "error": '<error message="{m}">trace</error>',
    }
    cases = []
    for name, outcome, message in results:
        child = children[outcome].format(m=message)
        cases.append(f'<testcase classname="flaws" name="{name}" time="0.1">{child}</testcase>')
    failures = sum(1 for _, outcome, _ in results if outcome == "failed")
    errors = sum(1 for _, outcome, _ in results if outcome == "error")
    xml = (
        f'<testsuites><testsuite name="pytest" errors="{errors}" failures="{failures}" skipped="0" '
        f'tests="{len(results)}">{"".join(cases)}</testsuite></testsuites>'
    )
    xml_path = tmp_path / "junit.xml"
    xml_path.write_text(xml, encoding="utf-8")
    return parse_junit(xml_path)


def test_all_flaws_failing_correctly_is_ok(tmp_path: Path) -> None:
    """WHAT: Eight AssertionError failures plus a passing control give no problems.
    WHY: This is the healthy outcome the real run must reach.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        good_results() -> verify_rows(...) == []
    """
    assert verify_rows(parse_results(tmp_path, good_results())) == []


def test_flaw_test_that_passed_is_a_problem(tmp_path: Path) -> None:
    """WHAT: If the F2 test passed, verification reports that F2 did not fail.
    WHY: A passing flaw test means the flaw does not exist on v1, which must be reported, not hidden.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        f2 outcome "passed" -> ["f2: expected outcome failed, got passed ..."]
    """
    results = replace_result(good_results(), FLAW_NAMES["f2"], "passed", "")
    problems = verify_rows(parse_results(tmp_path, results))
    assert len(problems) == 1 and problems[0].startswith("f2: expected outcome failed, got passed")


def test_error_with_import_error_is_a_problem(tmp_path: Path) -> None:
    """WHAT: If F1 ended as an error (ImportError) instead of a failure, verification rejects it.
    WHY: A test that cannot even import proves nothing about v1.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        f1 outcome "error" with "ImportError: no module named app" -> ["f1: expected outcome failed, got error ..."]
    """
    results = replace_result(good_results(), FLAW_NAMES["f1"], "error", "ImportError: no module named app")
    problems = verify_rows(parse_results(tmp_path, results))
    assert len(problems) == 1 and "f1: expected outcome failed, got error" in problems[0]


def test_failure_that_is_not_an_assertion_error_is_a_problem(tmp_path: Path) -> None:
    """WHAT: If F3 failed with a database error rather than an AssertionError, verification rejects it.
    WHY: Only an assertion about the scorecard value is proof of the flaw.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        f3 failed with "ProgrammingError: bind" -> ["f3: failed but not with an AssertionError: ..."]
    """
    results = replace_result(good_results(), FLAW_NAMES["f3"], "failed", "ProgrammingError: bind")
    problems = verify_rows(parse_results(tmp_path, results))
    assert len(problems) == 1 and problems[0].startswith("f3: failed but not with an AssertionError")


def test_failed_positive_control_is_a_problem(tmp_path: Path) -> None:
    """WHAT: If the positive control failed, verification reports it.
    WHY: A broken control means the set-up is wrong and every other result is suspect.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        control outcome "failed" -> ["test_positive_control: expected exactly one passed row, got ['failed']"]
    """
    results = replace_result(good_results(), CONTROL, "failed", "AssertionError: control broke")
    problems = verify_rows(parse_results(tmp_path, results))
    assert len(problems) == 1 and "test_positive_control" in problems[0]


def test_missing_flaw_test_and_unexpected_test_are_problems(tmp_path: Path) -> None:
    """WHAT: Dropping the F8 test and adding an unknown test gives two problems.
    WHY: The run must contain exactly the expected tests.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        no f8 row plus "test_extra" -> ["unexpected test in the run: test_extra", "f8: expected exactly one result, got 0"]
    """
    results = [item for item in good_results() if item[0] != FLAW_NAMES["f8"]]
    results.append(("test_extra", "passed", ""))
    problems = verify_rows(parse_results(tmp_path, results))
    assert any("unexpected test in the run: test_extra" in problem for problem in problems)
    assert any(problem.startswith("f8: expected exactly one result") for problem in problems)


def test_flaw_ids_and_report_rendering(tmp_path: Path) -> None:
    """WHAT: Checks flaw_id_of maps names to ids, every flaw id has a description, and the rendered report
    has one row per flaw, the verdict and the meta.
    WHY: results.md must be generated from the run, with one row for each flaw.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        "test_f4_scorecard_does_not_mix_tenants" -> "f4"; the report has 8 table rows starting "| F"
    """
    assert flaw_id_of(FLAW_NAMES["f4"]) == "f4" and flaw_id_of(CONTROL) is None
    assert set(FLAWS) == set(FLAW_NAMES)
    rows = parse_results(tmp_path, good_results())
    text = render_results(rows, {"tag": "v1-final"}, [])
    assert text.count(chr(10) + "| F") == 8 and "Verdict: OK" in text and "- tag: v1-final" in text
    assert "NOT OK" in render_results(rows, {}, ["something broke"])
