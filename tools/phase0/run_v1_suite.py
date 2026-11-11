# TERM: junit XML - a standard file pytest writes listing every test and its result.
# TERM: venv - a private Python environment so v1's packages never touch this machine's Python.
# TERM: collection error - pytest failed to even import a test file, so none of its tests ran.
# TERM: baseline - the recorded, reproducible result of v1's tests before any rewrite.
# TERM: compose service - one named container (e.g. redis) defined in v1's docker-compose.yml.
"""Run v1's (prv3 tag v1-final) pytest suite on a fresh clone and record the real results.

One-shot local script: it never edits, skips or filters v1 tests, and never retries.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

# sourced: the repository and tag created in Part B of this task.
V1_REPO_URL = "https://github.com/adithya-313/prv3"
V1_DEFAULT_TAG = "v1-final"
# sourced: read from v1's docker-compose.yml (the database service is NOT named "postgres").
DB_SERVICE_NAMES = ["timescaledb", "redis"]
ALLOWED_RUN_LABELS = ["no-services", "with-db"]
# policy: one hour ceiling for a full CPU run (also used for the large pip install).
PYTEST_TIMEOUT_SECONDS = 3600
PIP_TIMEOUT_SECONDS = 3600
# assumption: a shallow clone, venv creation or a version probe finishes well within 10 minutes.
SHORT_COMMAND_TIMEOUT_SECONDS = 600
# assumption: pulling two images and waiting for their health checks fits in 15 minutes.
DOCKER_TIMEOUT_SECONDS = 900
# policy: logs above 5 MB are trimmed so the repo stays small.
MAX_LOG_BYTES = 5 * 1024 * 1024
# policy: the last 2,000 lines of a trimmed log hold the final summary and tracebacks.
KEPT_LOG_LINES = 2000
VALID_OUTCOMES = ["passed", "failed", "error", "skipped"]


class JunitParseError(ValueError):
    """Raised when a junit XML file is malformed or has an unexpected shape."""


@dataclasses.dataclass
class InstallResult:
    """Outcome of one pip install step."""

    succeeded: bool
    return_code: int
    timed_out: bool
    wall_seconds: float
    log_path: Path


@dataclasses.dataclass
class RunResult:
    """Outcome of one pytest run."""

    run_label: str
    return_code: int
    timed_out: bool
    wall_seconds: float
    junit_path: Path
    stdout_path: Path
    stderr_path: Path


@dataclasses.dataclass
class TestRow:
    """One test case taken from the junit XML."""

    __test__ = False  # tells pytest this is data, not a test class

    file: str
    name: str
    outcome: str
    duration_seconds: float
    reason: str


@dataclasses.dataclass
class Summary:
    """Counts per outcome (one per result row) plus distinct (file, name) pairs, computed only from TestRow objects."""

    total: int
    passed: int
    failed: int
    error: int
    skipped: int
    distinct_names: int


@dataclasses.dataclass
class RunMeta:
    """Facts that make a run reproducible and auditable."""

    tag: str
    commit: str
    run_label: str
    timestamp_iso: str
    os_name: str
    python_version: str
    wall_seconds: float
    junit_sha256: str
    note: str


def as_text(value: str | bytes | None) -> str:
    """WHAT: Turns output captured from a subprocess into plain text.
    It accepts text, bytes or None and always returns a string.
    WHY: A timed-out subprocess hands back bytes or None, and we still want its partial output.

    Args:
        value: text, bytes or None from subprocess.
    Returns:
        A string ('' for None).
    Example:
        b"3 passed" -> "3 passed"; None -> ""
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_command(
    arguments: list[str], working_dir: Path | None, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    """WHAT: Runs one program with an argument list and a mandatory timeout.
    The text output is captured; the return code is NOT checked here.
    WHY: One place guarantees no shell=True and no call without a timeout.

    Args:
        arguments: program followed by its arguments.
        working_dir: folder to run in, or None for the current one.
        timeout_seconds: seconds before the process is killed.
    Returns:
        The finished process (stdout, stderr, returncode).
    Raises:
        subprocess.TimeoutExpired: when the timeout passes.
        FileNotFoundError: when the program does not exist.
    Example:
        run_command(["git", "--version"], None, 600) -> returncode 0
    """
    return subprocess.run(
        arguments,
        cwd=working_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        shell=False,
        check=False,
    )


def clone_v1(tag: str, target_dir: Path) -> Path:
    """WHAT: Makes a fresh shallow clone of v1 at the given tag into a new folder.
    The folder must not exist yet, so an old clone can never be reused.
    WHY: Reproducibility - the run must test exactly the tagged code and nothing stale.

    Args:
        tag: git tag to clone, e.g. "v1-final".
        target_dir: folder that must not exist.
    Returns:
        The path of the sentinel_flow_mvp folder inside the clone.
    Raises:
        FileExistsError: if target_dir exists.
        RuntimeError: if git clone fails.
    Example:
        clone_v1("v1-final", Path("tmp/v1")) -> Path("tmp/v1/sentinel_flow_mvp")
    """
    if target_dir.exists():
        raise FileExistsError(f"refusing to reuse existing folder: {target_dir}")
    arguments = ["git", "clone", "--depth", "1", "--branch", tag, V1_REPO_URL, str(target_dir)]
    finished = run_command(arguments, None, SHORT_COMMAND_TIMEOUT_SECONDS)
    if finished.returncode != 0:
        raise RuntimeError(f"git clone failed: {finished.stderr.strip()}")
    return target_dir / "sentinel_flow_mvp"


def read_commit_hash(clone_dir: Path) -> str:
    """WHAT: Reads the full commit hash the clone is sitting on.
    WHY: The hash proves which exact code the results belong to.

    Args:
        clone_dir: root folder of the clone.
    Returns:
        A 40-character hash.
    Raises:
        RuntimeError: if git rev-parse fails.
    Example:
        read_commit_hash(Path("tmp/v1")) -> "3b121e1e14ac77e8883864136e7409bce7c983aa"
    """
    finished = run_command(["git", "rev-parse", "HEAD"], clone_dir, SHORT_COMMAND_TIMEOUT_SECONDS)
    if finished.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {finished.stderr.strip()}")
    return finished.stdout.strip()


def find_python_311() -> Path:
    """WHAT: Asks the Windows `py` launcher for Python 3.11 and returns its path.
    It checks the reported version really is 3.11 and never falls back to another version.
    WHY: v1's Dockerfile uses python:3.11; another version would make results unrepresentative.

    Args:
        none.
    Returns:
        Path to python.exe of Python 3.11.
    Raises:
        RuntimeError: if the launcher or Python 3.11 is missing.
    Example:
        find_python_311() -> Path("C:/Users/me/AppData/Local/Programs/Python/Python311/python.exe")
    """
    probe = "import sys; print(sys.version_info[0], sys.version_info[1]); print(sys.executable)"
    try:
        finished = run_command(["py", "-3.11", "-c", probe], None, SHORT_COMMAND_TIMEOUT_SECONDS)
    except FileNotFoundError as error:
        raise RuntimeError("the 'py' launcher was not found; install Python 3.11") from error
    lines = finished.stdout.strip().splitlines()
    if finished.returncode != 0 or len(lines) != 2 or lines[0] != "3 11":
        raise RuntimeError("Python 3.11 not found via 'py -3.11'; refusing to use another version")
    return Path(lines[1])


def venv_python(venv: Path) -> Path:
    """WHAT: Returns the python executable inside a venv folder.
    WHY: Windows uses Scripts/python.exe while other systems use bin/python.

    Args:
        venv: venv folder.
    Returns:
        Path of the interpreter inside it.
    Example:
        venv_python(Path("x/.venv")) -> Path("x/.venv/Scripts/python.exe") on Windows
    """
    if platform.system() == "Windows":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def create_venv(python_path: Path, project_dir: Path) -> Path:
    """WHAT: Creates a brand-new venv folder named .venv inside the project folder.
    WHY: Isolation - v1's heavy packages (torch etc.) stay out of the system Python.

    Args:
        python_path: interpreter used to build the venv (3.11).
        project_dir: folder that will hold .venv.
    Returns:
        The venv folder path.
    Raises:
        RuntimeError: if venv creation fails.
    Example:
        create_venv(py311, Path("tmp/v1/sentinel_flow_mvp")) -> Path(".../.venv")
    """
    venv = project_dir / ".venv"
    arguments = [str(python_path), "-m", "venv", str(venv)]
    finished = run_command(arguments, None, SHORT_COMMAND_TIMEOUT_SECONDS)
    if finished.returncode != 0:
        raise RuntimeError(f"venv creation failed: {finished.stderr.strip()}")
    return venv


def pip_install(venv: Path, requirements_file: Path, log_path: Path) -> InstallResult:
    """WHAT: Runs `pip install -r <file>` in the venv and appends the full output to a log.
    A failure or timeout is returned as data, never hidden or patched around.
    WHY: A failed install is itself a finding about v1 on this machine.

    Args:
        venv: venv folder.
        requirements_file: file listing packages.
        log_path: log file to append to.
    Returns:
        InstallResult with success flag, return code and seconds.
    Example:
        pip_install(venv, Path("requirements.txt"), Path("pip.log")) -> InstallResult(succeeded=True, ...)
    """
    arguments = [str(venv_python(venv)), "-m", "pip", "install", "-r", str(requirements_file)]
    started = time.monotonic()
    timed_out = False
    try:
        finished = run_command(arguments, None, PIP_TIMEOUT_SECONDS)
        return_code, output = finished.returncode, finished.stdout + finished.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        return_code, output = -1, as_text(error.stdout) + as_text(error.stderr)
    elapsed = time.monotonic() - started
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"$ {' '.join(arguments)}\nreturn code: {return_code}\n{output}\n")
    return InstallResult(return_code == 0, return_code, timed_out, elapsed, log_path)


def install_requirements(venv: Path, project_dir: Path, log_path: Path) -> InstallResult:
    """WHAT: Installs v1's own requirements.txt into the venv, unchanged.
    WHY: The baseline must use v1's pinned packages; we never edit or skip any of them.

    Args:
        venv: venv folder.
        project_dir: sentinel_flow_mvp folder containing requirements.txt.
        log_path: pip log file.
    Returns:
        InstallResult (check .succeeded).
    Example:
        115-line requirements.txt installs -> InstallResult(succeeded=True, return_code=0, ...)
    """
    return pip_install(venv, project_dir / "requirements.txt", log_path)


def run_pytest(venv: Path, project_dir: Path, run_label: str, out_dir: Path) -> RunResult:
    """WHAT: Runs the whole v1 pytest suite from sentinel_flow_mvp with no filters.
    It only ignores tests/benchmarks and tests/load (scripts, not tests) and keeps the cache off.
    WHY: Honest evidence - no -x, no -k, no skips; crashes and timeouts are recorded too.

    Args:
        venv: venv folder with pytest installed.
        project_dir: sentinel_flow_mvp folder.
        run_label: "no-services" or "with-db".
        out_dir: folder for junit.xml and the stdout/stderr logs.
    Returns:
        RunResult with return code, timeout flag and wall-clock seconds.
    Side effects:
        Writes junit.xml, pytest.log and pytest-stderr.log into out_dir.
    Example:
        run_pytest(venv, proj, "no-services", out) -> RunResult(return_code=1, wall_seconds=312.4, ...)
    """
    junit_path = out_dir / "junit.xml"
    arguments = [
        str(venv_python(venv)),
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-rA",
        f"--junitxml={junit_path}",
        "--ignore=tests/benchmarks",
        "--ignore=tests/load",
    ]
    started = time.monotonic()
    timed_out = False
    try:
        finished = run_command(arguments, project_dir, PYTEST_TIMEOUT_SECONDS)
        return_code, stdout, stderr = finished.returncode, finished.stdout, finished.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        return_code, stdout, stderr = -1, as_text(error.stdout), as_text(error.stderr)
    elapsed = time.monotonic() - started
    stdout_path, stderr_path = out_dir / "pytest.log", out_dir / "pytest-stderr.log"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return RunResult(run_label, return_code, timed_out, elapsed, junit_path, stdout_path, stderr_path)


def first_line(text: str) -> str:
    """WHAT: Returns the first non-empty line of a text, stripped.
    WHY: Failure messages are long; one line keeps the results table readable.

    Args:
        text: any string, possibly multi-line or empty.
    Returns:
        The first non-blank line, or '' if none.
    Example:
        a text of a blank line, then "boom", then "traceback" -> "boom"
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def case_results(case: ElementTree.Element) -> list[tuple[str, str]]:
    """WHAT: Lists EVERY result a test case carries: one (outcome, reason) per <failure>, <error> or
    <skipped> child, in file order. A case with no such child is a single "passed".
    WHY: pytest can attach two results to one test (e.g. a call failure and a teardown error);
    keeping only the first would make the counts disagree with pytest's own summary.

    Args:
        case: a <testcase> element.
    Returns:
        List of (outcome, first line of the reason); never empty.
    Example:
        <testcase><error message="setup"/><error message="teardown"/></testcase> -> [("error", "setup"), ("error", "teardown")]
    """
    outcome_by_tag = {"failure": "failed", "error": "error", "skipped": "skipped"}
    results: list[tuple[str, str]] = []
    for child in case:
        if child.tag in outcome_by_tag:
            reason_text = child.get("message") or child.text or ""
            results.append((outcome_by_tag[child.tag], first_line(reason_text)))
    return results if results else [("passed", "")]


def read_suite_totals(root: ElementTree.Element, xml_path: Path) -> dict[str, int]:
    """WHAT: Adds up the tests/failures/errors/skipped attributes of every <testsuite> in the file.
    WHY: These are pytest's own totals; the parsed rows are checked against them.

    Args:
        root: XML root element.
        xml_path: file path, for error messages.
    Returns:
        Totals keyed by "tests", "failures", "errors", "skipped".
    Raises:
        JunitParseError: if a suite lacks a total attribute or it is not a whole number.
    Example:
        <testsuite tests="232" failures="27" errors="6" skipped="3"/> -> {"tests": 232, "failures": 27, "errors": 6, "skipped": 3}
    """
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        for key in totals:
            raw_value = suite.get(key)
            if raw_value is None or not raw_value.isdigit():
                raise JunitParseError(f"<testsuite> in {xml_path} lacks a numeric '{key}' attribute")
            totals[key] += int(raw_value)
    return totals


def check_totals(rows: list[TestRow], case_count: int, totals: dict[str, int], xml_path: Path) -> None:
    """WHAT: Compares the parsed rows with the suite totals and raises if ANY of them disagree.
    WHY: A parser that silently drops or double counts results would make the baseline untrustworthy.

    Args:
        rows: parsed rows.
        case_count: number of <testcase> elements.
        totals: from read_suite_totals.
        xml_path: file path, for error messages.
    Returns:
        Nothing.
    Raises:
        JunitParseError: listing every mismatch.
    Example:
        3 failed rows but failures="2" -> JunitParseError("... failed rows 3 != failures 2")
    """
    counted = {outcome: sum(1 for row in rows if row.outcome == outcome) for outcome in VALID_OUTCOMES}
    pairs = [
        ("test cases", case_count, totals["tests"]),
        ("failed rows", counted["failed"], totals["failures"]),
        ("error rows", counted["error"], totals["errors"]),
        ("skipped rows", counted["skipped"], totals["skipped"]),
    ]
    mismatches = [f"{label} {found} != {expected}" for label, found, expected in pairs if found != expected]
    if mismatches:
        raise JunitParseError(
            f"junit totals do not match parsed rows in {xml_path}: " + "; ".join(mismatches)
        )


def parse_junit(xml_path: Path) -> list[TestRow]:
    """WHAT: Reads a junit XML file and returns one TestRow per RESULT (a test with two results gives two rows),
    then reconciles the rows against the <testsuite> totals.
    Collection errors appear as rows too because pytest writes them as error testcases.
    WHY: Every reported number must come from the XML and agree with pytest's own totals.

    Args:
        xml_path: path of the junit XML file.
    Returns:
        List of TestRow (empty list only for a truly empty suite).
    Raises:
        JunitParseError: if the XML is malformed, has an unexpected root, lacks totals, or the totals disagree.
    Example:
        XML with 3 passing cases and tests="3" -> 3 TestRow objects, all outcome "passed"
    """
    try:
        root = ElementTree.parse(xml_path).getroot()
    except ElementTree.ParseError as error:
        raise JunitParseError(f"malformed junit XML in {xml_path}: {error}") from error
    if root.tag not in ("testsuites", "testsuite"):
        raise JunitParseError(f"unexpected root element <{root.tag}> in {xml_path}")
    rows: list[TestRow] = []
    case_count = 0
    for case in root.iter("testcase"):
        case_count += 1
        # pytest's xunit2 output has no file attribute; the classname is the module path.
        file_name = case.get("file") or case.get("classname") or ""
        duration = float(case.get("time") or 0.0)
        for outcome, reason in case_results(case):
            rows.append(TestRow(file_name, case.get("name") or "", outcome, duration, reason))
    check_totals(rows, case_count, read_suite_totals(root, xml_path), xml_path)
    return rows


def summarise(rows: list[TestRow]) -> Summary:
    """WHAT: Counts rows per outcome.
    WHY: Totals are derived from the rows only, so they cannot disagree with the table.

    Args:
        rows: parsed test rows.
    Returns:
        Summary of counts.
    Raises:
        ValueError: if a row has an outcome outside VALID_OUTCOMES.
    Example:
        3 passed, 1 failed -> Summary(total=4, passed=3, failed=1, error=0, skipped=0, distinct_names=4)
    """
    counts = {outcome: 0 for outcome in VALID_OUTCOMES}
    for row in rows:
        if row.outcome not in counts:
            raise ValueError(f"unknown outcome: {row.outcome}")
        counts[row.outcome] += 1
    distinct_names = len({(row.file, row.name) for row in rows})
    return Summary(
        len(rows), counts["passed"], counts["failed"], counts["error"], counts["skipped"], distinct_names
    )


def sha256_of_file(path: Path) -> str:
    """WHAT: Computes the SHA-256 fingerprint of a file.
    WHY: Anyone can later check the junit XML was not altered.

    Args:
        path: file to hash.
    Returns:
        64-character hex string.
    Example:
        a file containing b"abc" -> "ba7816bf8f01cfea..."
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_markdown(rows: list[TestRow], summary: Summary, meta: RunMeta) -> str:
    """WHAT: Builds the human-readable results.md: summary first, then a table of every test.
    WHY: Reviewers should see the verdict before the detail.

    Args:
        rows: parsed rows.
        summary: counts.
        meta: run facts.
    Returns:
        Markdown text.
    Example:
        2 rows -> text starting "# v1 test baseline (...)" followed by a 2-line table
    """
    lines = [f"# v1 test baseline ({meta.run_label})", ""]
    if meta.note:
        lines += [f"**Note:** {meta.note}", ""]
    lines += [
        f"- rows (one per result): {summary.total}, distinct (file, name) pairs: {summary.distinct_names}, passed: {summary.passed}, failed: {summary.failed}, "
        f"error: {summary.error}, skipped: {summary.skipped}"
    ]
    for key, value in dataclasses.asdict(meta).items():
        lines.append(f"- {key}: {value}")
    lines += ["", "| file | test | outcome | seconds | reason |", "|---|---|---|---|---|"]
    for row in rows:
        reason = row.reason.replace("|", "\\|")
        lines.append(f"| {row.file} | {row.name} | {row.outcome} | {row.duration_seconds:.3f} | {reason} |")
    return "\n".join(lines) + "\n"


def write_results(rows: list[TestRow], summary: Summary, meta: RunMeta, out_dir: Path) -> None:
    """WHAT: Writes results.json and results.md into out_dir.
    WHY: JSON is for machines, Markdown is for people; both hold the same data.

    Args:
        rows: parsed rows.
        summary: counts.
        meta: run facts.
        out_dir: output folder (created if missing).
    Returns:
        Nothing.
    Side effects:
        Creates or overwrites two files.
    Example:
        write_results(rows, summary, meta, Path("out")) -> out/results.json and out/results.md
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": dataclasses.asdict(meta),
        "summary": dataclasses.asdict(summary),
        "tests": [dataclasses.asdict(row) for row in rows],
    }
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out_dir / "results.md").write_text(render_markdown(rows, summary, meta), encoding="utf-8")


def reparse_results(out_dir: Path) -> Summary:
    """WHAT: Re-reads the saved junit.xml in an output folder with the current parser and rewrites
    results.json and results.md, keeping the saved meta (commit, timestamp, OS, seconds, hashes).
    WHY: Results made by an older parser can be corrected from the original evidence without re-running v1.

    Args:
        out_dir: folder holding junit.xml and results.json.
    Returns:
        The new Summary.
    Raises:
        JunitParseError: if the junit totals do not reconcile.
        FileNotFoundError: if junit.xml or results.json is missing.
    Example:
        a folder whose old summary said error=5 but junit says errors="6" -> new Summary(error=6, ...)
    """
    saved = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
    meta = RunMeta(**saved["meta"])
    if sha256_of_file(out_dir / "junit.xml") != meta.junit_sha256:
        raise ValueError("junit.xml does not match the SHA-256 saved in results.json")
    rows = parse_junit(out_dir / "junit.xml")
    summary = summarise(rows)
    write_results(rows, summary, meta, out_dir)
    return summary


def trim_log_if_large(log_path: Path) -> bool:
    """WHAT: If a log is over MAX_LOG_BYTES, keeps only its last KEPT_LOG_LINES lines.
    A first line says it was trimmed.
    WHY: Huge logs bloat the repo; the end of a log holds the summary.

    Args:
        log_path: log file.
    Returns:
        True if the file was trimmed.
    Example:
        a 9 MB log -> True; a 1 KB log -> False
    """
    if not log_path.exists() or log_path.stat().st_size <= MAX_LOG_BYTES:
        return False
    all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    kept = all_lines[-KEPT_LOG_LINES:]
    note = f"[TRIMMED: kept last {KEPT_LOG_LINES} of {len(all_lines)} lines]"
    log_path.write_text("\n".join([note] + kept) + "\n", encoding="utf-8")
    return True


def docker_available() -> bool:
    """WHAT: Checks that the Docker engine answers.
    WHY: Run 2 needs containers; if Docker is down we skip it and say so.

    Args:
        none.
    Returns:
        True when `docker info` succeeds.
    Example:
        Docker Desktop running -> True; not installed -> False
    """
    try:
        finished = run_command(["docker", "info"], None, SHORT_COMMAND_TIMEOUT_SECONDS)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return finished.returncode == 0


def compose_command(compose_file: Path, action: list[str]) -> None:
    """WHAT: Runs `docker compose -f <file> <action>` and raises if it fails.
    WHY: Used to start only v1's database and Redis, and to stop them without deleting volumes.

    Args:
        compose_file: v1's docker-compose.yml.
        action: e.g. ["up", "-d", "--wait", "redis"] or ["down"].
    Returns:
        Nothing.
    Raises:
        RuntimeError: on a non-zero return code.
    Example:
        compose_command(file, ["down"]) -> containers stopped, volumes kept
    """
    arguments = ["docker", "compose", "-f", str(compose_file)] + action
    finished = run_command(arguments, compose_file.parent, DOCKER_TIMEOUT_SECONDS)
    if finished.returncode != 0:
        raise RuntimeError(f"docker compose {action[0]} failed: {finished.stderr.strip()}")


def make_writable_and_retry(function: Callable[[str], object], path: str, _error: object) -> None:
    """WHAT: rmtree error handler that clears the read-only flag and retries.
    WHY: Git marks pack files read-only on Windows, which blocks deleting the temp clone.

    Args:
        function: the failed os function.
        path: the path it failed on.
        _error: unused error info.
    Returns:
        Nothing.
    Example:
        a read-only .git pack file -> chmod writable, delete succeeds
    """
    Path(path).chmod(stat.S_IWRITE)
    function(path)


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    """WHAT: Parses and validates the command line.
    WHY: A typo in the run label must stop the run instead of writing to a wrong folder.

    Args:
        argv: arguments after the program name.
    Returns:
        Namespace with run_label, tag and out_root.
    Raises:
        SystemExit: on an invalid run label.
    Example:
        ["--run-label", "with-db"] -> Namespace(run_label="with-db", ...)
    """
    parser = argparse.ArgumentParser(description="Record v1's real test results.")
    parser.add_argument("--run-label", required=True, choices=ALLOWED_RUN_LABELS)
    parser.add_argument("--tag", default=V1_DEFAULT_TAG)
    parser.add_argument("--out-root", default="docs/phase0/v1-test-results")
    return parser.parse_args(argv)


def build_meta(
    args: argparse.Namespace, commit: str, venv: Path, wall: float, sha: str, note: str
) -> RunMeta:
    """WHAT: Collects the reproducibility facts for results.json/md.
    WHY: Results without commit, OS and Python version cannot be trusted later.

    Args:
        args: parsed command line.
        commit: v1 commit hash.
        venv: venv folder (its Python version is read).
        wall: pytest wall-clock seconds.
        sha: SHA-256 of the junit XML, or "n/a".
        note: free-text finding, '' if none.
    Returns:
        A RunMeta.
    Example:
        wall=312.44 -> RunMeta(..., wall_seconds=312.4, ...)
    """
    version = run_command([str(venv_python(venv)), "--version"], None, SHORT_COMMAND_TIMEOUT_SECONDS)
    timestamp = datetime.now(UTC).isoformat()
    os_name = f"{platform.system()} {platform.release()} ({platform.version()})"
    return RunMeta(
        args.tag,
        commit,
        args.run_label,
        timestamp,
        os_name,
        version.stdout.strip(),
        round(wall, 1),
        sha,
        note,
    )


def record_outcome(
    args: argparse.Namespace,
    commit: str,
    venv: Path,
    out_dir: Path,
    result: RunResult | None,
    note: str,
) -> None:
    """WHAT: Parses the junit XML (if one exists), summarises, and writes the result files.
    WHY: A crash or failed install must still leave honest evidence behind.

    Args:
        args: parsed command line.
        commit: v1 commit hash.
        venv: venv folder.
        out_dir: output folder.
        result: RunResult, or None when pytest never ran.
        note: finding text to store.
    Returns:
        Nothing.
    Example:
        install failed -> result=None -> results.md says tests were not run
    """
    rows: list[TestRow] = []
    sha, wall = "n/a", 0.0
    if result is not None:
        wall = result.wall_seconds
        if result.junit_path.exists():
            rows = parse_junit(result.junit_path)
            sha = sha256_of_file(result.junit_path)
        else:
            note = (note + " No junit XML was produced.").strip()
        if result.timed_out:
            note = (note + " pytest hit the timeout and was killed.").strip()
    meta = build_meta(args, commit, venv, wall, sha, note)
    write_results(rows, summarise(rows), meta, out_dir)


def run_with_services(args: argparse.Namespace, project_dir: Path, venv: Path, out_dir: Path) -> RunResult:
    """WHAT: Runs pytest, starting only v1's database and Redis first when the label is with-db.
    WHY: Run 2 measures what changes when real services exist; they are always stopped after.

    Args:
        args: parsed command line.
        project_dir: sentinel_flow_mvp folder.
        venv: venv folder.
        out_dir: output folder.
    Returns:
        RunResult from pytest.
    Raises:
        RuntimeError: if services are needed but Docker is unavailable or compose fails.
    Example:
        "with-db" -> compose up timescaledb+redis, pytest, compose down
    """
    if args.run_label != "with-db":
        return run_pytest(venv, project_dir, args.run_label, out_dir)
    if not docker_available():
        raise RuntimeError("Docker is not available; skipping the with-db run")
    compose_file = project_dir / "docker-compose.yml"
    try:
        compose_command(compose_file, ["up", "-d", "--wait"] + DB_SERVICE_NAMES)
        return run_pytest(venv, project_dir, args.run_label, out_dir)
    finally:
        compose_command(compose_file, ["down"])


def execute_run(args: argparse.Namespace, work_root: Path, out_dir: Path) -> int:
    """WHAT: Runs every step in order: clone, venv, installs, optional services, pytest, record.
    WHY: Keeps main() short while keeping the order of steps visible in one place.

    Args:
        args: parsed command line.
        work_root: new temp folder.
        out_dir: output folder.
    Returns:
        0 on completion (test failures are results, not script errors).
    Raises:
        RuntimeError: when a step that must succeed fails (e.g. no Python 3.11).
    Example:
        --run-label no-services -> writes docs/.../no-services/* and returns 0
    """
    python_311 = find_python_311()
    project_dir = clone_v1(args.tag, work_root / "v1")
    commit = read_commit_hash(work_root / "v1")
    venv = create_venv(python_311, project_dir)
    pip_log = out_dir / "pip.log"
    pip_log.unlink(missing_ok=True)
    dev_file = Path(__file__).resolve().parents[2] / "requirements-dev.txt"
    installed = install_requirements(venv, project_dir, pip_log)
    if installed.succeeded:
        installed = pip_install(venv, dev_file, pip_log)  # v1 does not list pytest itself
    result = None
    note = "" if installed.succeeded else f"pip install failed (rc={installed.return_code}); tests not run."
    if installed.succeeded:
        result = run_with_services(args, project_dir, venv, out_dir)
    record_outcome(args, commit, venv, out_dir, result, note)
    for log_name in ["pip.log", "pytest.log", "pytest-stderr.log"]:
        trim_log_if_large(out_dir / log_name)
    return 0


def main(argv: list[str]) -> int:
    """WHAT: Entry point: validates arguments, makes a fresh temp folder, runs, then cleans up.
    Outputs are written straight to the output folder, outside the temp folder.
    WHY: The finally block guarantees the temp clone and venv are removed even on errors.

    Args:
        argv: command-line arguments.
    Returns:
        Exit code from execute_run.
    Example:
        main(["--run-label", "no-services"]) -> 0
    """
    args = parse_arguments(argv)
    out_dir = Path(args.out_root) / args.run_label
    out_dir.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix="v1_suite_"))
    try:
        return execute_run(args, work_root, out_dir)
    finally:
        shutil.rmtree(work_root, onerror=make_writable_and_retry)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
