# TERM: junit XML - a standard file pytest writes listing every test and its result.
# TERM: fixture file - a small hand-written XML file in tests/phase0/fixtures used as test input.
# TERM: collection error - pytest failed to import a test file, so none of its tests ran.
"""Unit tests for parse_junit and summarise. No network, no cloning, no subprocesses."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools.phase0.run_v1_suite import (
    JunitParseError,
    RunMeta,
    TestRow,
    parse_junit,
    reparse_results,
    sha256_of_file,
    summarise,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def parse_one(fixture_name: str) -> TestRow:
    """WHAT: Parses one fixture that holds exactly one test case and returns that row.
    WHY: Most tests below check a single case; this removes repeated setup.

    Args:
        fixture_name: file name inside tests/phase0/fixtures.
    Returns:
        The only TestRow in that file.
    Example:
        parse_one("passed.xml") -> TestRow(name="test_ok", outcome="passed", ...)
    """
    rows = parse_junit(FIXTURE_DIR / fixture_name)
    assert len(rows) == 1
    return rows[0]


def test_passed_case() -> None:
    """WHAT: A testcase with no children is read as passed, with its time and names.
    WHY: Without this, passing tests could be miscounted as failures.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        time="0.012" -> duration_seconds == 0.012, outcome "passed"
    """
    row = parse_one("passed.xml")
    assert row.outcome == "passed"
    assert row.file == "tests.test_a"
    assert row.name == "test_ok"
    assert row.duration_seconds == pytest.approx(0.012)
    assert row.reason == ""


def test_failed_case_keeps_first_line_of_reason() -> None:
    """WHAT: A <failure> child gives outcome failed and only the first reason line.
    WHY: The report needs the cause, but one line per test keeps it readable.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        message "assert 1 == 2" + newline + "where..." -> reason "assert 1 == 2"
    """
    row = parse_one("failed.xml")
    assert row.outcome == "failed"
    assert row.reason == "assert 1 == 2"


def test_skipped_case_keeps_reason() -> None:
    """WHAT: A <skipped> child gives outcome skipped with the skip message as reason.
    WHY: Skips must not count as passes, and the reason shows why they were skipped.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        message "needs database" -> outcome "skipped", reason "needs database"
    """
    row = parse_one("skipped.xml")
    assert row.outcome == "skipped"
    assert row.reason == "needs database"


def test_error_case() -> None:
    """WHAT: An <error> child gives outcome error, kept separate from failed.
    WHY: Setup crashes (e.g. no database) differ from wrong answers and must be countable.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        error message "failed on setup..." -> outcome "error"
    """
    row = parse_one("error.xml")
    assert row.outcome == "error"
    assert row.reason.startswith("failed on setup")


def test_collection_error_appears_as_row() -> None:
    """WHAT: A collection error (empty classname, <error> child) becomes an error row.
    WHY: A file that cannot be imported hides all its tests; dropping it would flatter v1.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        name "tests.integration.test_broken" -> 1 row, outcome "error"
    """
    row = parse_one("collection_error.xml")
    assert row.outcome == "error"
    assert row.name == "tests.integration.test_broken"
    assert row.reason == "collection failure"


def test_empty_suite_returns_no_rows() -> None:
    """WHAT: A valid suite with zero testcases returns an empty list.
    WHY: An empty suite is a real (if odd) result, different from a broken file.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        tests="0" -> []
    """
    assert parse_junit(FIXTURE_DIR / "empty.xml") == []


def test_malformed_xml_raises_clear_error() -> None:
    """WHAT: A truncated XML file raises JunitParseError instead of returning nothing.
    WHY: Silently returning [] would look like "no tests", hiding a crashed run.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        file cut off mid-tag -> JunitParseError("malformed junit XML ...")
    """
    with pytest.raises(JunitParseError, match="malformed junit XML"):
        parse_junit(FIXTURE_DIR / "malformed.xml")


def test_every_result_of_one_test_is_kept() -> None:
    """WHAT: A test case with two <error> children (setup and teardown) gives two error rows.
    WHY: Keeping only the first would make the error count disagree with pytest's own summary.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        one testcase with errors="2" -> 2 rows named test_two_errors, reasons "setup failed" and "teardown failed"
    """
    rows = parse_junit(FIXTURE_DIR / "multi_result.xml")
    assert [row.outcome for row in rows] == ["error", "error"]
    assert [row.reason for row in rows] == ["setup failed", "teardown failed"]
    assert summarise(rows).distinct_names == 1


def test_totals_mismatch_raises() -> None:
    """WHAT: A file whose <testsuite> says failures="0" but contains a <failure> raises JunitParseError.
    WHY: A disagreement between rows and pytest's own totals means the parse cannot be trusted.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        failures="0" with 1 failed row -> JunitParseError("... failed rows 1 != 0")
    """
    with pytest.raises(JunitParseError, match="failed rows 1 != 0"):
        parse_junit(FIXTURE_DIR / "totals_mismatch.xml")


def test_missing_totals_raise() -> None:
    """WHAT: A <testsuite> without tests/failures/errors/skipped attributes raises JunitParseError.
    WHY: Without totals there is nothing to reconcile against, so the file is not accepted silently.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        <testsuite name="pytest"> with no counts -> JunitParseError("lacks a numeric 'tests' attribute")
    """
    with pytest.raises(JunitParseError, match="lacks a numeric"):
        parse_junit(FIXTURE_DIR / "no_totals.xml")


def test_summarise_counts_match_rows() -> None:
    """WHAT: Builds rows from all single-case fixtures and checks every count.
    WHY: Totals must come from the rows, so the summary can never disagree with the table.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        1 passed + 1 failed + 1 skipped + 2 error -> Summary(total=5, passed=1, ...)
    """
    names = ["passed.xml", "failed.xml", "skipped.xml", "error.xml", "collection_error.xml"]
    rows = [parse_one(name) for name in names]
    summary = summarise(rows)
    assert summary.total == len(rows) == 5
    assert (summary.passed, summary.failed, summary.skipped, summary.error) == (1, 1, 1, 2)


def test_summarise_rejects_unknown_outcome() -> None:
    """WHAT: summarise raises ValueError for an outcome it does not know.
    WHY: A typo must not be silently dropped from the totals.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        outcome "weird" -> ValueError
    """
    bad_row = TestRow("f", "n", "weird", 0.0, "")
    with pytest.raises(ValueError):
        summarise([bad_row])


def test_reparse_results_rewrites_summary_from_saved_junit(tmp_path: Path) -> None:
    """WHAT: Copies a junit fixture and an out-of-date results.json into a temp folder, then checks
    reparse_results rewrites the summary from the junit file and keeps the saved meta.
    WHY: Old results must be fixable from the original evidence, and only if that evidence is unchanged (SHA-256).

    Args:
        tmp_path: pytest's temporary folder.
    Returns:
        Nothing.
    Example:
        stale summary error=1, junit with errors="2" -> results.json afterwards has error=2
    """
    shutil.copy(FIXTURE_DIR / "multi_result.xml", tmp_path / "junit.xml")
    meta = RunMeta("t", "c", "label", "now", "os", "py", 1.0, sha256_of_file(tmp_path / "junit.xml"), "")
    stale = {"meta": meta.__dict__, "summary": {"error": 1}, "tests": []}
    (tmp_path / "results.json").write_text(json.dumps(stale), encoding="utf-8")
    new_summary = reparse_results(tmp_path)
    saved = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert new_summary.error == 2 == saved["summary"]["error"]
    assert saved["meta"]["commit"] == "c"
