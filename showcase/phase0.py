# TERM: showcase - a live demonstration that re-runs every Phase 0 check on this machine and reports the result.
# TERM: exit gate - a pass/fail condition that must hold before Phase 0 can be called finished.
# TERM: drift - a checker's freshly rendered file differs from the file committed in docs/.
# TERM: baseline - stored data used only as the thing a live result is compared against (never as a live result).
# TERM: junit XML - a standard file pytest writes listing every test and its result.
"""Run every Phase 0 check live and compute the exit gate G1-G6.

Nothing here reads stored results as if they were live. The only stored data used are (a) the committed
docker-no-services counts, as the baseline for G5, and (b) the committed renders in docs/, as the baseline
for the drift check. Every number in the report comes from a command run now.
"""
from __future__ import annotations

import dataclasses
import difflib
import json
import platform
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from tools.phase0.run_scorecard_flaws import CONTROL_TEST_NAME, FLAWS, flaw_id_of, verify_rows
from tools.phase0.run_v1_suite import JunitParseError, TestRow, parse_junit, summarise

# policy: quick probes of the machine (git, docker, python versions) either answer within a minute or are unavailable.
PROBE_TIMEOUT_SECONDS = 60
# policy: medoRa's own unit suite takes a few seconds; fifteen minutes only guards against a hang.
SUITE_TIMEOUT_SECONDS = 900
# policy: a checker clones v1 (data_provenance also clones the full history of v1-legacy), so allow thirty minutes.
CHECKER_TIMEOUT_SECONDS = 1800
# policy: the flaw runner clones v1 and runs one short container; thirty minutes guards against a hang.
FLAWS_TIMEOUT_SECONDS = 1800
# policy: the v1 suite took about five minutes in the saved image; thirty minutes guards against a hang.
V1_RERUN_TIMEOUT_SECONDS = 1800
OUTPUT_TAIL_LINES = 30
ENV_IMAGE_NAME = "v1-baseline-env"
RECORDED_IMAGE_META = Path("docs/phase0/v1-test-results/docker-with-db/meta.json")
BASELINE_RESULTS = Path("docs/phase0/v1-test-results/docker-no-services/results.json")
REUSE_ENV_FROM = "docs/phase0/v1-test-results/docker-with-db"
PASS, FAIL, NOT_RUN = "PASS", "FAIL", "NOT RUN"
UNAVAILABLE = "unavailable"
# Matches an ISO date-time such as 2026-10-22T18:20:23 or a clock time such as 18:20:23.
TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}|\b\d{2}:\d{2}:\d{2}\b")
# Each checker: (check id, title, script, files it renders: (rendered name inside --out, committed path in docs/)).
CHECKERS = [
    ("feature_truth", "Feature-truth table", "tools/phase0/feature_truth.py", [("feature-truth.md", "docs/phase0/feature-truth/feature-truth.md")]),
    ("data_provenance", "Data provenance audit", "tools/phase0/data_provenance.py",
     [("provenance.md", "docs/phase0/data-provenance/provenance.md"), ("measurements.json", "docs/phase0/data-provenance/measurements.json")]),
    ("screen_inventory", "Screen and function inventory", "tools/phase0/screen_inventory.py", [("screens.md", "docs/phase0/screens/screens.md")]),
    ("adr_000", "ADR-000 carry-over decisions", "tools/phase0/adr_000.py", [("ADR-000-v1-carry-over.md", "docs/adr/ADR-000-v1-carry-over.md")]),
]


@dataclasses.dataclass
class CommandResult:
    """What one subprocess did. exit_code is None when it could not start or timed out."""

    arguments: list[str]
    exit_code: int | None
    output: str
    seconds: float
    timed_out: bool
    error: str


@dataclasses.dataclass
class Fact:
    """One machine fact for the report header; available is False when its command failed."""

    label: str
    value: str
    available: bool


@dataclasses.dataclass
class Drift:
    """A rendered file that differs from the committed one, with the exact differing lines and why."""

    file: str
    committed_path: str
    differing_lines: list[tuple[str, str]]


@dataclasses.dataclass
class CheckResult:
    """The outcome of one live check, with everything the report shows for it."""

    check_id: str
    title: str
    claim: str
    command: str
    exit_code: int | None
    status: str
    output: str
    numbers: dict[str, str]
    notes: list[str]
    drifts: list[Drift]
    seconds: float


@dataclasses.dataclass
class Gate:
    """One exit-gate condition and whether it held."""

    gate_id: str
    claim: str
    passed: bool
    status: str
    detail: str


@dataclasses.dataclass
class Report:
    """The whole Phase 0 showcase result."""

    facts: list[Fact]
    checks: list[CheckResult]
    gates: list[Gate]
    passed: bool
    started: str
    total_seconds: float


Runner = Callable[[list[str], int], CommandResult]


class SubprocessRunner:
    """Runs programs with a list of arguments (never a shell), a timeout, and captured UTF-8 output.

    A failing or missing program never raises; it returns a CommandResult whose exit_code is None or non-zero,
    so every caller must check the exit code explicitly.
    """

    def __init__(self, working_dir: Path) -> None:
        """WHAT: Remembers the folder every command runs in.
        WHY: All showcase commands run from the repo root.

        Args:
            working_dir: the folder to run commands in.
        Returns:
            Nothing.
        Example:
            SubprocessRunner(Path(".")) -> a runner for the current folder
        """
        self.working_dir = working_dir

    def __call__(self, arguments: list[str], timeout_seconds: int) -> CommandResult:
        """WHAT: Runs one command and returns what it did: exit code, combined output, seconds, and whether it
        timed out or could not start.
        WHY: One place guarantees list arguments, a timeout and an explicit exit code for every call.

        Args:
            arguments: program followed by its arguments.
            timeout_seconds: seconds before the process is killed.
        Returns:
            CommandResult (exit_code None when it timed out or could not start).
        Example:
            runner(["git", "--version"], 60) -> CommandResult(exit_code=0, output="git version ...", ...)
        """
        started = time.monotonic()
        try:
            finished = subprocess.run(arguments, cwd=self.working_dir, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=timeout_seconds, shell=False, check=False)
        except subprocess.TimeoutExpired as error:
            partial = decode_partial(error.stdout)
            return CommandResult(arguments, None, partial, time.monotonic() - started, True, f"timed out after {timeout_seconds} s")
        except OSError as error:
            return CommandResult(arguments, None, "", time.monotonic() - started, False, f"{type(error).__name__}: {error}")
        return CommandResult(arguments, finished.returncode, finished.stdout + finished.stderr, time.monotonic() - started, False, "")


def decode_partial(value: str | bytes | None) -> str:
    """WHAT: Turns the partial output of a timed-out process (text, bytes or None) into text.
    WHY: A timeout still shows whatever the command printed before it was killed.

    Args:
        value: stdout captured so far.
    Returns:
        Text ('' for None).
    Example:
        b"abc" -> "abc"; None -> ""
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def probe(runner: Runner, label: str, arguments: list[str]) -> Fact:
    """WHAT: Runs one probe command and returns its first output line as a Fact; any failure gives the value
    "unavailable" with available False (shown in red).
    WHY: Each machine fact must come from a real command.

    Args:
        runner: command runner.
        label: fact label.
        arguments: the command.
    Returns:
        A Fact.
    Example:
        probe(runner, "Docker", ["docker", "--version"]) -> Fact("Docker", "Docker version 29.0.1, ...", True)
    """
    result = runner(arguments, PROBE_TIMEOUT_SECONDS)
    lines = [line.strip() for line in result.output.splitlines() if line.strip()]
    if result.exit_code != 0 or not lines:
        return Fact(label, UNAVAILABLE, False)
    return Fact(label, lines[0], True)


def collect_facts(runner: Runner, started: str) -> list[Fact]:
    """WHAT: Gathers the report-header facts: git commit, dirty-tree flag, start time, OS, CPU, RAM, Python and
    Docker version. The OS comes from Python's platform module; CPU and RAM from PowerShell (Windows).
    WHY: A reader must know exactly which code and machine produced the result.

    Args:
        runner: command runner.
        started: start time text (ISO, UTC).
    Returns:
        The facts, in display order.
    Example:
        a clean checkout -> [Fact("Commit", "b0461b6...", True), Fact("Working tree", "clean", True), ...]
    """
    commit = probe(runner, "Commit", ["git", "rev-parse", "HEAD"])
    status = runner(["git", "status", "--porcelain"], PROBE_TIMEOUT_SECONDS)
    if status.exit_code != 0:
        tree = Fact("Working tree", UNAVAILABLE, False)
    else:
        tree = Fact("Working tree", "dirty (uncommitted changes)" if status.output.strip() else "clean", True)
    cpu_command = ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"]
    ram_command = ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"]
    ram = probe(runner, "RAM (bytes)", ram_command)
    return [commit, tree, Fact("Started (UTC)", started, True), Fact("OS", platform.platform(), True), probe(runner, "CPU", cpu_command), ram,
            probe(runner, "Python", [sys.executable, "--version"]), probe(runner, "Docker", ["docker", "--version"])]


def tail(text: str, count: int = OUTPUT_TAIL_LINES) -> str:
    """WHAT: Returns the last `count` lines of a text.
    WHY: The report shows the end of each command's real output, where its verdict is.

    Args:
        text: command output.
        count: number of lines to keep.
    Returns:
        The last lines joined with newlines.
    Example:
        a 100-line text, count 30 -> the last 30 lines
    """
    return chr(10).join(text.splitlines()[-count:])


def classify_difference(line: str) -> str:
    """WHAT: Gives the reason a differing line is reported: "run-specific timestamp" when it contains a date-time
    or clock time, otherwise "content changed".
    WHY: Lines that differ because of the clock are listed and explained, never silently ignored.

    Args:
        line: one differing line (without its +/- marker).
    Returns:
        The reason text.
    Example:
        "- timestamp_iso: 2026-10-22T18:20:23" -> "run-specific timestamp"
    """
    return "run-specific timestamp" if TIMESTAMP_PATTERN.search(line) else "content changed"


def compare_render(committed_path: Path, rendered_path: Path, label: str) -> Drift | None:
    """WHAT: Compares a freshly rendered file with the committed one (as text, so line-ending style does not
    matter) and returns the exact differing lines with a reason each, or None when they are identical.
    A render that was not produced at all counts as a drift.
    WHY: Any difference between a live render and the committed file means the committed file is stale.

    Args:
        committed_path: the committed file in docs/ (the baseline being compared against).
        rendered_path: the file the checker just rendered.
        label: file name to show.
    Returns:
        A Drift, or None when identical.
    Example:
        one changed line -> Drift(file="x.md", differing_lines=[("-old", "content changed"), ("+new", "content changed")])
    """
    if not rendered_path.is_file():
        return Drift(label, str(committed_path), [("(no render was produced)", "checker did not write this file")])
    committed = committed_path.read_text(encoding="utf-8").splitlines()
    rendered = rendered_path.read_text(encoding="utf-8").splitlines()
    differing: list[tuple[str, str]] = []
    for line in difflib.unified_diff(committed, rendered, lineterm="", n=0):
        is_change = line[:1] in ("+", "-") and not line.startswith(("+++", "---"))
        if is_change:
            differing.append((line, classify_difference(line[1:])))
    return Drift(label, str(committed_path), differing) if differing else None


def last_nonempty_line(text: str) -> str:
    """WHAT: Returns the last non-empty line of a text ('' if none).
    WHY: A checker's last line is its verdict, shown as its result line.

    Args:
        text: command output.
    Returns:
        The line, stripped.
    Example:
        "a" + newline + "OK: done" + newline -> "OK: done"
    """
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def exited_zero(checks: dict[str, CheckResult], check_id: str) -> bool:
    """WHAT: True when the named check's command exited with code 0.
    WHY: Most gates are decided by one checker's exit code.

    Args:
        checks: results by check id.
        check_id: which check.
    Returns:
        True for exit code 0 (not for None or any other code).
    Example:
        exit_code 0 -> True; exit_code None (timed out) -> False
    """
    return checks[check_id].exit_code == 0


def run_unit_suite(runner: Runner, temp_dir: Path) -> CheckResult:
    """WHAT: Runs medoRa's own unit suite (cache off, junit XML into temp_dir) and takes its counts from the
    parsed junit file (the existing reconciling parser), not from the console text.
    WHY: Proves the repo's tests pass right now.

    Args:
        runner: command runner.
        temp_dir: folder for the junit file.
    Returns:
        A CheckResult: PASS when pytest exits 0 and no test failed or errored.
    Example:
        a green suite -> CheckResult(status="PASS", numbers={"passed": "75", ...})
    """
    junit_path = temp_dir / "suite-junit.xml"
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", f"--junitxml={junit_path}", "tests"]
    result = runner(command, SUITE_TIMEOUT_SECONDS)
    numbers: dict[str, str] = {}
    notes: list[str] = []
    status = FAIL
    if junit_path.is_file():
        try:
            summary = summarise(parse_junit(junit_path))
        except JunitParseError as error:
            notes.append(f"the junit file could not be reconciled: {error}")
        else:
            numbers = {"passed": str(summary.passed), "failed": str(summary.failed), "error": str(summary.error), "skipped": str(summary.skipped)}
            suite_passed = result.exit_code == 0 and summary.failed == 0 and summary.error == 0
            if suite_passed:
                status = PASS
            else:
                status = FAIL
    else:
        notes.append("pytest wrote no junit file")
    return CheckResult("suite", "medoRa unit suite", "medoRa's own tests pass right now.", " ".join(command), result.exit_code, status,
                       result.output, numbers, notes, [], result.seconds)


def run_checker(runner: Runner, temp_dir: Path, repo_root: Path, check: tuple) -> CheckResult:
    """WHAT: Runs one checker with --out <temp>/<id>, then compares every file it rendered with the committed
    file in docs/ and records any drift.
    WHY: The checker's exit code proves its data still holds; the drift check proves the committed render is current.

    Args:
        runner: command runner.
        temp_dir: temp folder.
        repo_root: the medoRa repo root.
        check: one entry of CHECKERS.
    Returns:
        A CheckResult: PASS when the exit code is 0.
    Example:
        feature_truth exits 0 and its render equals the committed table -> PASS with no drifts
    """
    check_id, title, script, outputs = check
    out_dir = temp_dir / check_id
    command = [sys.executable, script, "--out", str(out_dir)]
    result = runner(command, CHECKER_TIMEOUT_SECONDS)
    drifts: list[Drift] = []
    if result.exit_code == 0:
        for rendered_name, committed in outputs:
            drift = compare_render(repo_root / committed, out_dir / rendered_name, rendered_name)
            if drift is not None:
                drifts.append(drift)
    status = PASS if result.exit_code == 0 else FAIL
    last_line = last_nonempty_line(result.output)
    return CheckResult(check_id, title, f"{title}: every item is covered and every piece of evidence verifies against a fresh v1-final clone.",
                       " ".join(command), result.exit_code, status, result.output, {"result line": last_line, "files that drifted": str(len(drifts))},
                       [], drifts, result.seconds)


def evaluate_flaw_rows(rows: list[TestRow]) -> tuple[bool, dict[str, str], list[str]]:
    """WHAT: Judges the scorecard flaw run from its parsed junit rows: all 8 flaw tests must have failed with an
    AssertionError and the positive control must have passed (the existing verify_rows rules).
    WHY: A flaw test that passed means the flaw is gone; one that errored proves nothing; a failed control
    means the set-up is broken.

    Args:
        rows: parsed junit rows of the flaw run.
    Returns:
        (all good, key numbers, problems).
    Example:
        8 AssertionError failures plus a passing control -> (True, {"flaws failed with AssertionError": "8", ...}, [])
    """
    problems = verify_rows(rows)
    failed_with_assertion = 0
    for row in rows:
        if flaw_id_of(row.name) in FLAWS and row.outcome == "failed" and row.reason.startswith("AssertionError"):
            failed_with_assertion += 1
    control_passed = any(row.name == CONTROL_TEST_NAME and row.outcome == "passed" for row in rows)
    control_text = "yes" if control_passed else "no"
    numbers = {"flaws failed with AssertionError": str(failed_with_assertion), "positive control passed": control_text}
    return (not problems and failed_with_assertion == len(FLAWS) and control_passed), numbers, problems


def run_scorecard_flaws(runner: Runner, temp_dir: Path) -> CheckResult:
    """WHAT: Runs tools/phase0/run_scorecard_flaws.py with --out <temp>/flaws, then re-judges the junit it wrote
    with evaluate_flaw_rows (so the verdict does not rest on the runner's exit code alone).
    WHY: Proves the 8 vendor-scorecard flaws exist on v1 right now, for the right reason.

    Args:
        runner: command runner.
        temp_dir: temp folder.
    Returns:
        A CheckResult: PASS only if the runner exited 0 and the junit shows 8 correct failures and a passing control.
    Example:
        a good run -> PASS with numbers {"flaws failed with AssertionError": "8", "positive control passed": "yes"}
    """
    out_dir = temp_dir / "flaws"
    command = [sys.executable, "tools/phase0/run_scorecard_flaws.py", "--out", str(out_dir)]
    result = runner(command, FLAWS_TIMEOUT_SECONDS)
    notes: list[str] = []
    numbers: dict[str, str] = {}
    good = False
    junit_path = out_dir / "junit.xml"
    if junit_path.is_file():
        try:
            good, numbers, problems = evaluate_flaw_rows(parse_junit(junit_path))
        except JunitParseError as error:
            notes.append(f"the junit file could not be reconciled: {error}")
        else:
            notes += problems
    else:
        notes.append("the runner wrote no junit file")
    status = PASS if good and result.exit_code == 0 else FAIL
    return CheckResult("scorecard_flaws", "Vendor-scorecard flaws", "8 designed flaw tests fail on v1 with an AssertionError and the positive control passes.",
                       " ".join(command), result.exit_code, status, result.output, numbers, notes, [], result.seconds)


def read_counts(results_json: Path) -> dict[str, int]:
    """WHAT: Reads passed/failed/error/skipped from a results.json summary.
    WHY: Both the live v1 rerun and the committed baseline are compared through this one reader.

    Args:
        results_json: path to a results.json written by the v1 suite runner.
    Returns:
        The four counts.
    Example:
        a summary with passed 197, failed 27, error 6, skipped 3 -> {"passed": 197, "failed": 27, "error": 6, "skipped": 3}
    """
    summary = json.loads(results_json.read_text(encoding="utf-8"))["summary"]
    return {name: int(summary[name]) for name in ("passed", "failed", "error", "skipped")}


def not_run(title: str, claim: str, reason: str) -> CheckResult:
    """WHAT: Builds a CheckResult with status NOT RUN and the reason.
    WHY: A step that could not run is reported honestly (and fails its gate), never skipped quietly.

    Args:
        title: check title.
        claim: what the check would prove.
        reason: why it did not run.
    Returns:
        A CheckResult with status NOT RUN.
    Example:
        not_run("v1 rerun", "...", "image missing") -> CheckResult(status="NOT RUN", notes=["image missing"], ...)
    """
    return CheckResult("v1_rerun", title, claim, "(not run)", None, NOT_RUN, "", {}, [reason], [], 0.0)


def rerun_v1_suite(runner: Runner, repo_root: Path, temp_dir: Path) -> CheckResult:
    """WHAT: Reruns v1's suite (no-services label) from the SAVED environment image into <temp>/v1, then compares
    its live passed/failed/error/skipped counts with the committed docker-no-services results.json, which is used
    only as the labelled baseline. If Docker is down, the image is missing, or its ID differs from the recorded one,
    the step is NOT RUN (nothing is rebuilt).
    WHY: Proves v1 still behaves exactly as recorded, in the same environment.

    Args:
        runner: command runner.
        repo_root: the medoRa repo root.
        temp_dir: temp folder.
    Returns:
        A CheckResult: PASS when the four live counts equal the baseline.
    Example:
        live 197/27/6/3 vs baseline 197/27/6/3 -> PASS
    """
    title, claim = "v1 suite rerun", "v1's own test suite, run now in the saved image, gives the same counts as the recorded baseline."
    recorded = json.loads((repo_root / RECORDED_IMAGE_META).read_text(encoding="utf-8"))["env_image_id"]
    inspect = runner(["docker", "image", "inspect", ENV_IMAGE_NAME, "--format", "{{.Id}}"], PROBE_TIMEOUT_SECONDS)
    if inspect.exit_code != 0:
        return not_run(title, claim, f"the saved image {ENV_IMAGE_NAME} could not be inspected (is Docker running?): {tail(inspect.output, 3)}")
    if inspect.output.strip() != recorded:
        return not_run(title, claim, f"image ID {inspect.output.strip()} differs from the recorded {recorded}; not rebuilding")
    out_root = temp_dir / "v1"
    command = [sys.executable, "-m", "tools.phase0.run_v1_suite_docker", "--run-label", "docker-no-services", "--reuse-env-from", REUSE_ENV_FROM, "--out-root", str(out_root)]
    result = runner(command, V1_RERUN_TIMEOUT_SECONDS)
    live_path = out_root / "docker-no-services" / "results.json"
    if result.exit_code != 0 or not live_path.is_file():
        return CheckResult(
            check_id="v1_rerun",
            title=title,
            claim=claim,
            command=" ".join(command),
            exit_code=result.exit_code,
            status=FAIL,
            output=result.output,
            numbers={},
            notes=["the rerun did not produce results"],
            drifts=[],
            seconds=result.seconds,
        )
    live, baseline = read_counts(live_path), read_counts(repo_root / BASELINE_RESULTS)
    numbers = {f"live {name}": str(live[name]) for name in live} | {f"baseline {name} (committed docker-no-services)": str(baseline[name]) for name in baseline}
    status = PASS if live == baseline else FAIL
    return CheckResult("v1_rerun", title, claim, " ".join(command), result.exit_code, status, result.output, numbers, [], [], result.seconds)


def drifted_files(checks: dict[str, CheckResult]) -> list[str]:
    """WHAT: Lists every rendered file that drifted, across all checkers.
    WHY: Gate G6 requires that none did.

    Args:
        checks: results by check id.
    Returns:
        File names.
    Example:
        feature_truth drifted on feature-truth.md -> ["feature-truth.md"]
    """
    names: list[str] = []
    for check in checks.values():
        names += [drift.file for drift in check.drifts]
    return names


def compute_gates(checks: dict[str, CheckResult]) -> list[Gate]:
    """WHAT: Computes the exit gate G1-G6 from the live check results:
    G1 feature_truth exit 0; G2 data_provenance exit 0; G3 the scorecard-flaw check passed; G4 adr_000 exit 0;
    G5 the live v1 counts equal the baseline (NOT RUN counts as failed); G6 the unit suite passed, the screen
    inventory exited 0 and no checker render drifted.
    WHY: One function decides the gate so the page, the console and the tests all agree.

    Args:
        checks: results by check id (suite, feature_truth, data_provenance, screen_inventory, adr_000, scorecard_flaws, v1_rerun).
    Returns:
        The six gates in order.
    Example:
        every check PASS and no drift -> six gates all passed
    """
    ft_ok = exited_zero(checks, "feature_truth")
    if ft_ok:
        ft_status = PASS
    else:
        ft_status = FAIL
    g1 = Gate("G1", "Every feature has a status and verified evidence", ft_ok, ft_status, f"feature_truth exit {checks['feature_truth'].exit_code}")

    dp_ok = exited_zero(checks, "data_provenance")
    if dp_ok:
        dp_status = PASS
    else:
        dp_status = FAIL
    g2 = Gate("G2", "Every data file has a provenance row", dp_ok, dp_status, f"data_provenance exit {checks['data_provenance'].exit_code}")

    g3_parts: list[str] = []
    for k, v in checks["scorecard_flaws"].numbers.items():
        g3_parts.append(f"{k}: {v}")
    g3_detail = "; ".join(g3_parts)
    g3 = Gate("G3", "Scorecard flaws fail on v1 as expected and the positive control passes", checks["scorecard_flaws"].status == PASS, checks["scorecard_flaws"].status, g3_detail)

    adr_ok = exited_zero(checks, "adr_000")
    if adr_ok:
        adr_status = PASS
    else:
        adr_status = FAIL
    g4 = Gate("G4", "ADR-000 covers every item", adr_ok, adr_status, f"adr_000 exit {checks['adr_000'].exit_code}")

    g5_detail = "; ".join(checks["v1_rerun"].notes) or "see the v1 rerun section"
    g5 = Gate("G5", "Live v1 counts equal the committed baseline", checks["v1_rerun"].status == PASS, checks["v1_rerun"].status, g5_detail)

    drift_names = drifted_files(checks)
    g6_passed = checks["suite"].status == PASS and exited_zero(checks, "screen_inventory") and not drift_names
    g6 = Gate("G6", "medoRa's own suite passes and no checker render drifted", g6_passed, PASS if g6_passed else FAIL, f"suite {checks['suite'].status}, screen_inventory exit {checks['screen_inventory'].exit_code}, drifted files: {len(drift_names)}")

    return [g1, g2, g3, g4, g5, g6]


def exit_code_for(gates: list[Gate]) -> int:
    """WHAT: Returns 0 only if every gate passed, otherwise 1.
    WHY: The command's exit code is the machine-readable verdict.

    Args:
        gates: the computed gates.
    Returns:
        0 or 1.
    Example:
        six passed gates -> 0; one failed gate -> 1
    """
    return 0 if all(gate.passed for gate in gates) else 1


def run_phase0(repo_root: Path, runner: Runner | None = None) -> Report:
    """WHAT: Runs every Phase 0 check live (unit suite, four checkers with drift checks, scorecard flaws, v1 rerun),
    collects the machine facts, computes the gates and returns the Report. The temp folder is removed afterwards.
    WHY: This is the single entry point the command line and the tests share.

    Args:
        repo_root: the medoRa repo root (commands run there).
        runner: command runner (default: the real one).
    Returns:
        The Report.
    Side effects:
        Runs many subprocesses; creates and removes a temp folder.
    Example:
        run_phase0(Path(".")) -> Report(passed=True, gates=[G1..G6 all passed], ...)
    """
    run = runner or SubprocessRunner(repo_root)
    started_clock = time.monotonic()
    started = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    facts = collect_facts(run, started)
    with tempfile.TemporaryDirectory(prefix="showcase_phase0_") as temp_name:
        temp_dir = Path(temp_name)
        checks = [run_unit_suite(run, temp_dir)]
        checks += [run_checker(run, temp_dir, repo_root, entry) for entry in CHECKERS]
        checks += [run_scorecard_flaws(run, temp_dir), rerun_v1_suite(run, repo_root, temp_dir)]
    by_id = {check.check_id: check for check in checks}
    gates = compute_gates(by_id)
    return Report(facts, checks, gates, exit_code_for(gates) == 0, started, time.monotonic() - started_clock)
