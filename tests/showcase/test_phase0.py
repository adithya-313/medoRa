# TERM: exit gate - the pass/fail conditions G1-G6 that decide whether Phase 0 is finished.
# TERM: drift - a freshly rendered file differs from the committed one.
# TERM: fake runner - a small stand-in for the subprocess runner that returns canned results (no Docker, no network).
# TERM: escaping - turning < > & into harmless text so command output cannot run as page code.
"""Tests for the Phase 0 showcase. They call the real gate, drift, rerun and render functions and only fake the
subprocess runner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import showcase.__main__ as showcase_main
from showcase.phase0 import (
    CHECKERS,
    FAIL,
    NOT_RUN,
    PASS,
    CheckResult,
    CommandResult,
    Drift,
    Fact,
    Report,
    compare_render,
    compute_gates,
    evaluate_flaw_rows,
    exit_code_for,
    rerun_v1_suite,
    run_checker,
)
from showcase.render import render_report
from tools.phase0.run_scorecard_flaws import CONTROL_TEST_NAME, FLAWS
from tools.phase0.run_v1_suite import TestRow

CHECK_IDS = ["suite", "feature_truth", "data_provenance", "screen_inventory", "adr_000", "scorecard_flaws", "v1_rerun"]
RECORDED_ID = "sha256:recorded"
BASELINE = {"passed": 197, "failed": 27, "error": 6, "skipped": 3}


def make_check(check_id: str, exit_code: int | None = 0, status: str = PASS, drifts: list[Drift] | None = None, output: str = "ok") -> CheckResult:
    """WHAT: Builds a CheckResult with the given exit code, status, drifts and output.
    WHY: The gate tests start from an all-passing set and change one thing.

    Args:
        check_id: which check.
        exit_code: exit code (None for did not finish).
        status: PASS, FAIL or NOT RUN.
        drifts: drifted files.
        output: command output.
    Returns:
        A CheckResult.
    Example:
        make_check("adr_000", exit_code=1, status="FAIL") -> a failed ADR check
    """
    return CheckResult(check_id, f"title {check_id}", "claim", "cmd", exit_code, status, output, {"k": "v"}, [], drifts or [], 1.0)


def passing_checks() -> dict[str, CheckResult]:
    """WHAT: Builds a set of check results in which everything passed.
    WHY: Baseline for the gate tests.

    Args:
        none.
    Returns:
        Results by check id.
    Example:
        passing_checks()["suite"].status -> "PASS"
    """
    return {check_id: make_check(check_id) for check_id in CHECK_IDS}


def test_gate_fails_when_any_one_checker_exits_non_zero() -> None:
    """WHAT: With everything passing all six gates pass and the exit code is 0; making each checker exit 1 in
    turn fails the matching gate (feature_truth G1, data_provenance G2, adr_000 G4, screen_inventory G6) and the
    exit code becomes 1.
    WHY: One failing checker must stop Phase 0 from being called finished.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        adr_000 exit 1 -> G4 fails and exit_code_for(...) == 1
    """
    gates = compute_gates(passing_checks())
    assert [gate.gate_id for gate in gates] == ["G1", "G2", "G3", "G4", "G5", "G6"]
    assert all(gate.passed for gate in gates) and exit_code_for(gates) == 0
    expected = {"feature_truth": "G1", "data_provenance": "G2", "adr_000": "G4", "screen_inventory": "G6"}
    for check_id, gate_id in expected.items():
        checks = passing_checks()
        checks[check_id] = make_check(check_id, exit_code=1, status=FAIL)
        failed = [gate.gate_id for gate in compute_gates(checks) if not gate.passed]
        assert failed == [gate_id]
        assert exit_code_for(compute_gates(checks)) == 1
    timed_out = passing_checks()
    timed_out["feature_truth"] = make_check("feature_truth", exit_code=None, status=FAIL)
    assert not compute_gates(timed_out)[0].passed


def test_drift_is_detected_and_reports_the_differing_lines(tmp_path: Path) -> None:
    """WHAT: compare_render returns None for identical files, and for a changed file returns the exact removed and
    added lines, labelling a line with a date-time as a run-specific timestamp and any other as changed content;
    a missing render is a drift too.
    WHY: Drift must be visible with its lines and reasons, never ignored.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        a "generated 2026-10-22T18:20" line that changed -> reason "run-specific timestamp"
    """
    committed = tmp_path / "committed.md"
    rendered = tmp_path / "rendered.md"
    committed.write_text("same" + chr(10) + "generated 2026-10-22T18:20" + chr(10) + "old text" + chr(10), encoding="utf-8")
    rendered.write_text("same" + chr(10) + "generated 2026-10-23T09:00" + chr(10) + "new text" + chr(10), encoding="utf-8")
    drift = compare_render(committed, rendered, "x.md")
    assert drift is not None
    reasons = {line: reason for line, reason in drift.differing_lines}
    assert reasons["-generated 2026-10-22T18:20"] == "run-specific timestamp" and reasons["+new text"] == "content changed"
    rendered.write_text(committed.read_text(encoding="utf-8"), encoding="utf-8")
    assert compare_render(committed, rendered, "x.md") is None
    missing = compare_render(committed, tmp_path / "nothing.md", "x.md")
    assert missing is not None and "no render was produced" in missing.differing_lines[0][0]


def changed_render_runner(arguments: list[str], timeout_seconds: int) -> CommandResult:
    """WHAT: Pretends to be a checker that exits 0 and renders a changed table.md into its --out folder (the last argument).
    WHY: Lets run_checker be tested without running a real checker.

    Args:
        arguments: the command; the last argument is the --out folder.
        timeout_seconds: ignored.
    Returns:
        A successful CommandResult.
    Example:
        changed_render_runner([..., "--out", "x"], 5) -> CommandResult(exit_code=0, ...) and x/table.md written
    """
    out_dir = Path(arguments[-1])
    out_dir.mkdir(parents=True)
    (out_dir / "table.md").write_text("new" + chr(10), encoding="utf-8")
    return CommandResult(arguments, 0, "OK: fake", 0.1, False, "")


def test_checker_drift_fails_gate_g6(tmp_path: Path) -> None:
    """WHAT: run_checker, with a fake checker that renders a file different from the committed one, reports the
    drift with its differing lines even though the checker exited 0, and gate G6 then fails with the drift count.
    WHY: A stale committed render must stop the exit gate.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        committed "old", rendered "new" -> drift lines ("-old", ...) and ("+new", ...); G6 fails
    """
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "table.md").write_text("old" + chr(10), encoding="utf-8")
    entry = ("feature_truth", "Feature table", "tools/x.py", [("table.md", "docs/table.md")])
    check = run_checker(changed_render_runner, tmp_path / "temp", tmp_path, entry)
    assert check.exit_code == 0 and len(check.drifts) == 1 and ("+new", "content changed") in check.drifts[0].differing_lines
    checks = passing_checks()
    checks["feature_truth"] = check
    gates = compute_gates(checks)
    assert [gate.gate_id for gate in gates if not gate.passed] == ["G6"] and "drifted files: 1" in gates[5].detail


def make_rerun_root(tmp_path: Path) -> Path:
    """WHAT: Builds a fake repo root holding the recorded image id and the committed baseline counts.
    WHY: rerun_v1_suite reads both; tests change only the fake runner.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        The fake repo root.
    Example:
        make_rerun_root(tmp_path) -> a folder with docs/phase0/v1-test-results/docker-with-db/meta.json
    """
    results = tmp_path / "docs" / "phase0" / "v1-test-results"
    (results / "docker-with-db").mkdir(parents=True)
    (results / "docker-no-services").mkdir(parents=True)
    (results / "docker-with-db" / "meta.json").write_text(json.dumps({"env_image_id": RECORDED_ID}), encoding="utf-8")
    (results / "docker-no-services" / "results.json").write_text(json.dumps({"summary": BASELINE}), encoding="utf-8")
    return tmp_path


class RerunRunner:
    """A fake runner for rerun_v1_suite: docker inspect answers as configured, and the v1 rerun command writes a
    results.json with the configured live counts into its --out-root folder."""

    def __init__(self, image_exit: int, image_id: str, live_counts: dict[str, int]) -> None:
        """WHAT: Stores how docker inspect should answer and which counts the pretend rerun reports.
        WHY: Lets the NOT RUN, PASS and FAIL paths be tested without Docker.

        Args:
            image_exit: exit code of docker inspect.
            image_id: its output.
            live_counts: counts the pretend rerun reports.
        Returns:
            Nothing.
        Example:
            RerunRunner(1, "", {}) -> a runner whose docker inspect fails
        """
        self.image_exit = image_exit
        self.image_id = image_id
        self.live_counts = live_counts

    def __call__(self, arguments: list[str], timeout_seconds: int) -> CommandResult:
        """WHAT: Answers a docker command with the configured result, or pretends to run the v1 suite.
        WHY: The fake stands in for the real subprocess runner.

        Args:
            arguments: the command.
            timeout_seconds: ignored.
        Returns:
            A CommandResult.
        Example:
            runner(["docker", "image", "inspect", ...], 60) -> CommandResult(exit_code=0, output="sha256:recorded")
        """
        if arguments[0] == "docker":
            return CommandResult(arguments, self.image_exit, self.image_id, 0.1, False, "")
        out_dir = Path(arguments[arguments.index("--out-root") + 1]) / "docker-no-services"
        out_dir.mkdir(parents=True)
        (out_dir / "results.json").write_text(json.dumps({"summary": self.live_counts}), encoding="utf-8")
        return CommandResult(arguments, 0, "done", 0.1, False, "")


def test_missing_image_makes_g5_not_run_and_fails_the_gate(tmp_path: Path) -> None:
    """WHAT: With docker inspect failing, or returning a different image id, rerun_v1_suite gives NOT RUN and
    never starts the rerun; equal live and baseline counts give PASS; different counts give FAIL. A NOT RUN
    step makes gate G5 NOT RUN and not passed, and the exit code 1.
    WHY: A step that could not run must be shown, and must fail the gate.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        docker inspect exit 1 -> status "NOT RUN", G5 "NOT RUN", exit_code_for == 1
    """
    root = make_rerun_root(tmp_path)
    missing = rerun_v1_suite(RerunRunner(1, "", {}), root, tmp_path / "t1")
    assert missing.status == NOT_RUN and missing.exit_code is None
    wrong = rerun_v1_suite(RerunRunner(0, "sha256:other", {}), root, tmp_path / "t2")
    assert wrong.status == NOT_RUN and "differs from the recorded" in wrong.notes[0]
    checks = passing_checks()
    checks["v1_rerun"] = missing
    gates = compute_gates(checks)
    assert gates[4].status == NOT_RUN and not gates[4].passed and exit_code_for(gates) == 1
    assert rerun_v1_suite(RerunRunner(0, RECORDED_ID, BASELINE), root, tmp_path / "t3").status == PASS
    assert rerun_v1_suite(RerunRunner(0, RECORDED_ID, {**BASELINE, "failed": 28}), root, tmp_path / "t4").status == FAIL


def flaw_rows(flaw_outcome: str = "failed", control_outcome: str = "passed", reason: str = "AssertionError: v1 returned x") -> list[TestRow]:
    """WHAT: Builds junit rows for the eight flaw tests plus the control; the first flaw uses flaw_outcome.
    WHY: The flaw-check tests change one row at a time.

    Args:
        flaw_outcome: outcome of the first flaw test.
        control_outcome: outcome of the positive control.
        reason: failure reason of the flaw tests.
    Returns:
        The rows.
    Example:
        flaw_rows("passed") -> the first flaw passed (the flaw is gone)
    """
    names = ["test_f1_a", "test_f2_a", "test_f3_a", "test_f4_a", "test_f5_a", "test_f6_a", "test_f7_a", "test_f8_a"]
    rows = [TestRow("flaws", name, "failed", 0.1, reason) for name in names]
    rows[0] = TestRow("flaws", names[0], flaw_outcome, 0.1, reason if flaw_outcome == "failed" else "")
    rows.append(TestRow("flaws", CONTROL_TEST_NAME, control_outcome, 0.1, ""))
    return rows


def test_flaw_check_rejects_a_passed_flaw_and_a_failed_control() -> None:
    """WHAT: evaluate_flaw_rows accepts 8 AssertionError failures plus a passing control, and rejects a flaw that
    passed, a flaw that failed with another error, and a control that failed.
    WHY: Each of those would mean the flaw run proves nothing about v1.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        first flaw passed -> (False, ..., ["f1: expected outcome failed, got passed ..."])
    """
    assert len(FLAWS) == 8
    good, numbers, problems = evaluate_flaw_rows(flaw_rows())
    assert good and problems == [] and numbers["flaws failed with AssertionError"] == "8"
    passed_flaw, _, passed_problems = evaluate_flaw_rows(flaw_rows(flaw_outcome="passed"))
    assert not passed_flaw and any("got passed" in problem for problem in passed_problems)
    other_error, _, _ = evaluate_flaw_rows(flaw_rows(reason="ProgrammingError: bind"))
    assert not other_error
    bad_control, numbers_bad, _ = evaluate_flaw_rows(flaw_rows(control_outcome="failed"))
    assert not bad_control and numbers_bad["positive control passed"] == "no"


def test_html_escapes_script_and_has_no_external_resources() -> None:
    """WHAT: A report whose command output, notes, drift lines and machine facts contain <script> tags renders
    with those tags escaped, and the page contains no script element and no external src or href.
    WHY: Command output must never run as code when the page is opened.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        output "<script>alert(1)</script>" -> "&lt;script&gt;alert(1)&lt;/script&gt;" in the page
    """
    attack = "<script>alert(1)</script>"
    drift = Drift("x.md", "docs/x.md", [("+" + attack, "content changed")])
    check = make_check("suite", output="line one" + chr(10) + attack)
    check.drifts = [drift]
    check.notes = [attack]
    check.numbers = {attack: attack}
    checks = [check] + [make_check(check_id) for check_id in CHECK_IDS if check_id != "suite"]
    report = Report([Fact("Commit", attack, True), Fact("Docker", "unavailable", False)], checks, compute_gates({c.check_id: c for c in checks}), True, "2026-10-22 18:00:00", 12.0)
    page = render_report(report)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script" not in page and "src=" not in page and "href=" not in page
    assert page.startswith("<!DOCTYPE html>") and 'class="red">unavailable' in page


def test_command_exit_code_is_one_when_a_gate_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """WHAT: The real command-line main, with run_phase0 replaced by a function returning a report with one failed
    gate, writes the HTML page and returns 1; with all gates passing it returns 0; an unsupported phase returns 2.
    WHY: The process exit code is the machine-readable verdict.

    Args:
        tmp_path: pytest temporary folder.
        monkeypatch: pytest monkeypatch.
    Returns:
        Nothing.
    Example:
        one failed gate -> main(["run", "--phase", "0"]) == 1
    """
    (tmp_path / "tools" / "phase0").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    passing = [make_check(check_id) for check_id in CHECK_IDS]
    failing = [make_check(check_id) for check_id in CHECK_IDS]
    failing[1] = make_check("feature_truth", exit_code=1, status=FAIL)
    for checks, expected in ((passing, 0), (failing, 1)):
        gates = compute_gates({check.check_id: check for check in checks})
        report = Report([], checks, gates, exit_code_for(gates) == 0, "now", 1.0)
        monkeypatch.setattr(showcase_main, "run_phase0", lambda repo_root, report=report: report)
        assert showcase_main.main(["run", "--phase", "0"]) == expected
        assert (tmp_path / "showcase" / "out" / "phase-0.html").is_file()
    assert showcase_main.main(["run", "--phase", "5"]) == 2
    assert len(CHECKERS) == 4
