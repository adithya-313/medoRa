# TERM: flaw test - a test that states CORRECT behaviour and is expected to FAIL on v1 (tests/v1_flaws).
# TERM: positive control - a test that must PASS on v1; it proves the test set-up itself works.
# TERM: saved image - the Docker image v1-baseline-env with v1's packages installed (built in an earlier task).
# TERM: junit XML - a standard file pytest writes listing every test and its result.
# TERM: AssertionError - Python's error for a failed "assert"; the proof that a test failed for the right reason.
"""Run the v1 scorecard flaw tests inside the saved image against a fresh v1-final clone, then VERIFY
that every flaw test failed with an AssertionError and the positive control passed.

No database or Redis is needed, so Docker Compose is not started. Nothing is installed anywhere.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

# Run as `python tools/phase0/run_scorecard_flaws.py`, so the repo root must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.phase0.run_v1_suite import (  # noqa: E402
    V1_DEFAULT_TAG,
    JunitParseError,
    TestRow,
    clone_v1,
    make_writable_and_retry,
    parse_junit,
    read_commit_hash,
    run_command,
    sha256_of_file,
)
from tools.phase0.run_v1_suite_docker import ENV_IMAGE_NAME, image_id  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
FLAWS_DIR = REPO_ROOT / "tests" / "v1_flaws"
RECORDED_META = REPO_ROOT / "docs" / "phase0" / "v1-test-results" / "docker-with-db" / "meta.json"
CONTROL_TEST_NAME = "test_positive_control"
# policy: the flaw tests are tiny and need no services; ten minutes is a generous ceiling.
RUN_TIMEOUT_SECONDS = 600
# sourced: flaw id -> (short title, v1 line range) as documented in the Phase 0 task and verified in
# docs/phase0/feature-truth/features.yaml (supplier-scorecard) against v1-final.
FLAWS = {
    "f1": ("no orders -> TRUSTED with rate 100", "30-39"),
    "f2": ("score = 100 - 15 x failures, not a rate", "73"),
    "f3": ("pending orders not yet due count as failures", "55-59"),
    "f4": ("no tenant filter: every shop's orders are mixed", "26-28"),
    "f5": ("money computed as float, not exact Decimal", "44, 62"),
    "f6": ("failures never age: no time window", "45-65"),
    "f7": ("negative loss when cost_price > expected_mrp", "61-62"),
    "f8": ("success rate truncated by int()", "70"),
}


def flaw_id_of(test_name: str) -> str | None:
    """WHAT: Returns "f1".."f8" for a flaw test name such as "test_f3_pending_orders...", else None.
    WHY: Verification needs to match each result row to its flaw.

    Args:
        test_name: the test function name.
    Returns:
        The flaw id, or None when the name is not a flaw test.
    Example:
        "test_f3_pending_orders_not_yet_due_are_not_failures" -> "f3"; "test_positive_control" -> None
    """
    match = re.match(r"test_(f[1-9])_", test_name)
    return match.group(1) if match else None


def verify_rows(rows: list[TestRow]) -> list[str]:
    """WHAT: Checks the junit rows against the expectation: every flaw test F1-F8 is present exactly once
    with outcome "failed" and a reason starting "AssertionError"; the positive control passed; nothing else ran.
    WHY: A flaw test that passed means the flaw does not exist; one that errored (for example ImportError)
    proves nothing about v1; a failed control means the set-up is broken.

    Args:
        rows: parsed junit rows (already reconciled with the junit totals).
    Returns:
        A list of problems (empty when everything holds).
    Example:
        a run where test_f2 passed -> ["f2: expected outcome failed, got passed (the flaw does not show on v1)"]
    """
    problems: list[str] = []
    by_flaw: dict[str, list[TestRow]] = {}
    controls: list[TestRow] = []
    for row in rows:
        flaw_id = flaw_id_of(row.name)
        if flaw_id is not None:
            by_flaw.setdefault(flaw_id, []).append(row)
        elif row.name == CONTROL_TEST_NAME:
            controls.append(row)
        else:
            problems.append(f"unexpected test in the run: {row.name}")
    for flaw_id in FLAWS:
        problems += check_flaw_rows(flaw_id, by_flaw.get(flaw_id, []))
    if len(controls) != 1 or controls[0].outcome != "passed":
        outcomes = [row.outcome for row in controls]
        problems.append(f"{CONTROL_TEST_NAME}: expected exactly one passed row, got {outcomes}")
    return problems


def check_flaw_rows(flaw_id: str, flaw_rows: list[TestRow]) -> list[str]:
    """WHAT: Checks the rows of one flaw test: exactly one row, outcome failed, reason is an AssertionError.
    WHY: Keeps verify_rows short and gives each wrong outcome its own clear message.

    Args:
        flaw_id: "f1".."f8".
        flaw_rows: the rows whose test name belongs to this flaw.
    Returns:
        Problems for this flaw (empty when fine).
    Example:
        one row failed with "ImportError: no module" -> ["f1: failed but not with an AssertionError: ImportError: ..."]
    """
    if len(flaw_rows) != 1:
        return [f"{flaw_id}: expected exactly one result, got {len(flaw_rows)}"]
    row = flaw_rows[0]
    if row.outcome != "failed":
        return [f"{flaw_id}: expected outcome failed, got {row.outcome} ({row.reason or 'no reason'})"]
    if not row.reason.startswith("AssertionError"):
        return [f"{flaw_id}: failed but not with an AssertionError: {row.reason}"]
    return []


def render_results(rows: list[TestRow], meta: dict[str, str], problems: list[str]) -> str:
    """WHAT: Builds results.md: the verdict, one row per flaw (flaw, v1 lines, what v1 returned versus what is
    correct, outcome), the control, any verification problems, and the run meta.
    WHY: The report is generated from the run, never typed by hand.

    Args:
        rows: parsed junit rows.
        meta: tag, commit, image id, junit sha256, timestamp, machine.
        problems: output of verify_rows.
    Returns:
        Markdown text.
    Example:
        all 8 flaws failing correctly -> "Verdict: OK ..." followed by an 8-row table
    """
    verdict = (
        "OK: every flaw test failed with an AssertionError and the positive control passed"
        if not problems
        else "NOT OK: " + str(len(problems)) + " verification problem(s)"
    )
    lines = [
        "# v1 vendor-scorecard flaw tests",
        "",
        f"Verdict: {verdict}",
        "",
        "| flaw | what is wrong | v1 lines | what v1 returned vs what is correct | outcome |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        flaw_id = flaw_id_of(row.name)
        if flaw_id is not None and flaw_id in FLAWS:
            message = row.reason.removeprefix("AssertionError: ").replace("|", "/")
            title, v1_lines = FLAWS[flaw_id]
            lines.append(f"| {flaw_id.upper()} | {title} | {v1_lines} | {message} | {row.outcome} |")
    control = [row.outcome for row in rows if row.name == CONTROL_TEST_NAME]
    lines += ["", f"Positive control ({CONTROL_TEST_NAME}): {control[0] if control else 'missing'}", ""]
    lines += [f"- PROBLEM: {problem}" for problem in problems]
    lines += ["", "## Run meta", ""] + [f"- {key}: {value}" for key, value in meta.items()]
    return chr(10).join(lines) + chr(10)


def check_saved_image() -> str:
    """WHAT: Confirms the saved image exists, has the recorded image ID, and contains pytest.
    WHY: The flaw run must use exactly the environment of the earlier baseline, and must not install anything.

    Args:
        none.
    Returns:
        The image ID.
    Raises:
        RuntimeError: if the ID differs from the recorded one or pytest is missing (STOP conditions).
    Example:
        matching image with pytest -> "sha256:f1fa9c40..."
    """
    recorded = json.loads(RECORDED_META.read_text(encoding="utf-8"))["env_image_id"]
    current = image_id(ENV_IMAGE_NAME)
    if current != recorded:
        raise RuntimeError(f"image ID {current} differs from the recorded {recorded}; stopping")
    probe = run_command(
        ["docker", "run", "--rm", ENV_IMAGE_NAME, "python", "-m", "pip", "show", "pytest"],
        None,
        RUN_TIMEOUT_SECONDS,
    )
    if probe.returncode != 0:
        raise RuntimeError("pytest is not installed in the saved image; stopping (nothing is installed)")
    return current


def run_in_container(project_dir: Path, out_dir: Path) -> None:
    """WHAT: Runs pytest on tests/v1_flaws inside the saved image: v1's folder is mounted read-only at /work
    (it is on PYTHONPATH), the flaw tests read-only at /flaws, results go to /out. Cache off, junit XML on.
    pytest's exit code is NOT an error here (the flaw tests are meant to fail); the junit file is checked later.
    WHY: Same environment as the baseline, no services needed.

    Args:
        project_dir: the sentinel_flow_mvp folder of a fresh v1-final clone.
        out_dir: output folder (receives junit.xml and pytest.log).
    Returns:
        Nothing.
    Raises:
        RuntimeError: if no junit.xml was produced.
    Example:
        a normal run -> out_dir/junit.xml with 9 test cases
    """
    arguments = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{project_dir.as_posix()}:/work:ro",
        "-v",
        f"{FLAWS_DIR.as_posix()}:/flaws:ro",
        "-v",
        f"{out_dir.resolve().as_posix()}:/out",
        "-w",
        "/work",
        ENV_IMAGE_NAME,
        "python",
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-rA",
        "--junitxml=/out/junit.xml",
        "/flaws",
    ]
    finished = run_command(arguments, None, RUN_TIMEOUT_SECONDS)
    (out_dir / "pytest.log").write_text(finished.stdout + finished.stderr, encoding="utf-8")
    if not (out_dir / "junit.xml").exists():
        raise RuntimeError("pytest produced no junit.xml; see pytest.log")


def main(argv: list[str]) -> int:
    """WHAT: Checks the image, clones v1-final fresh, runs the flaw tests in the container, parses the junit
    XML (which reconciles its totals), verifies the outcomes, writes results.md, and returns 1 on any problem.
    The temporary clone is always removed.
    WHY: One command proves, with real output, that each flaw is present in v1 for the right reason.

    Args:
        argv: command-line arguments.
    Returns:
        0 when every check holds, otherwise 1.
    Side effects:
        Writes junit.xml, pytest.log and results.md into the output folder.
    Example:
        main([]) with 8 correct failures and a passing control -> 0
    """
    parser = argparse.ArgumentParser(description="Run and verify the v1 scorecard flaw tests.")
    parser.add_argument("--tag", default=V1_DEFAULT_TAG)
    parser.add_argument("--out", default="docs/phase0/scorecard-flaws")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    image = check_saved_image()
    work_root = Path(tempfile.mkdtemp(prefix="scorecard_flaws_"))
    try:
        project_dir = clone_v1(args.tag, work_root / "v1")
        commit = read_commit_hash(work_root / "v1")
        run_in_container(project_dir, out_dir)
    finally:
        shutil.rmtree(work_root, onerror=make_writable_and_retry)
    return verify_and_report(out_dir, args.tag, commit, image)


def verify_and_report(out_dir: Path, tag: str, commit: str, image: str) -> int:
    """WHAT: Parses junit.xml, verifies the rows, writes results.md and returns the exit code.
    WHY: Splits the checking and reporting from the Docker work so main() stays short.

    Args:
        out_dir: folder holding junit.xml.
        tag: v1 tag.
        commit: v1 commit hash.
        image: image ID used.
    Returns:
        0 when verification found no problems, otherwise 1.
    Example:
        a junit file whose totals do not reconcile -> results.md says NOT OK and the result is 1
    """
    meta = {
        "tag": tag,
        "commit": commit,
        "image_id": image,
        "timestamp": datetime.now(UTC).isoformat(),
        "machine": platform.platform(),
        "junit_sha256": sha256_of_file(out_dir / "junit.xml"),
    }
    try:
        rows = parse_junit(out_dir / "junit.xml")
        problems = verify_rows(rows)
    except JunitParseError as error:
        rows, problems = [], [f"junit XML could not be reconciled: {error}"]
    (out_dir / "results.md").write_text(render_results(rows, meta, problems), encoding="utf-8")
    for problem in problems:
        print("PROBLEM:", problem)
    print("OK" if not problems else "NOT OK")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
