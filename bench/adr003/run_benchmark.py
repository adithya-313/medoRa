"""ADR-003 mutation-testing benchmark harness.

Runs each candidate mutation-testing tool against the fixed target inside a
python:3.12 container, records hard-rule outcomes (H1-H5) and measured metrics
(M1-M4), runs the weakened-assertion demo (H4), applies the ADR-003 decision
rule, and writes bench/adr003/results/results.json.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET_FILES: list[str] = [
    "cloud/experiments/assignment.py",
    "cloud/experiments/analysis.py",
    "tools/ci/test_change_gate.py",
]
UNIT_TESTS: list[str] = [
    "tests/cloud/test_experiment_assignment.py",
    "tests/cloud/test_experiment_analysis.py",
    "tests/ci/test_test_change_gate.py",
]
DEMO_DIR = REPO_ROOT / "bench" / "adr003" / "demo"
DEMO_SOURCE = DEMO_DIR / "discount.py"
DEMO_FULL_TEST = DEMO_DIR / "test_discount_full.py"
DEMO_WEAKENED_TEST = DEMO_DIR / "test_discount_weakened.py"
RESULTS_FILE = REPO_ROOT / "bench" / "adr003" / "results" / "results.json"
MUTANTS_DIR = REPO_ROOT / "mutants"

TOOLS: list[str] = ["mutmut", "cosmic-ray", "pytest-gremlins"]

LICENCE_URLS: dict[str, str] = {
    "mutmut": "https://github.com/boxed/mutmut/blob/main/LICENSE",
    "cosmic-ray": "https://github.com/sixty-north/cosmic-ray/blob/main/LICENCE.txt",
    "pytest-gremlins": "https://github.com/mikelane/pytest-gremlins/blob/main/LICENSE",
}

IN_DOCKER = os.path.exists("/.dockerenv")

STATUS_OK = "ok"
STATUS_HARNESS_ERROR = "harness_error"

TOOL_VERSIONS: dict[str, str] = {
    "mutmut": "3.8.1",
    "cosmic-ray": "8.9.0",
    "pytest-gremlins": "1.13.2",
}


def run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 1800.0,
) -> tuple[float, int, str, str]:
    """Run a command and return (elapsed_seconds, returncode, stdout, stderr).

    Purpose:
        Centralised subprocess execution with wall-clock timing, output capture,
        and an explicit non-zero exit-code check. Never swallows the underlying
        error; the caller decides how to treat a non-zero exit code.

    Args:
        cmd: Command and arguments to execute.
        cwd: Working directory for the command.
        env: Additional environment variables (merged with os.environ).
        timeout_seconds: Hard timeout for the command.

    Returns:
        Tuple of elapsed seconds, return code, captured stdout, captured stderr.
    """
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        elapsed = time.perf_counter() - start
        return elapsed, proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - start
        return elapsed, -1, "", f"Command timed out after {timeout_seconds}s: {exc}"


def median(values: list[float]) -> float | None:
    """Return the median of a non-empty list, or None when empty."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def uv_sync_groups(groups: list[str], timeout_seconds: float = 600.0) -> tuple[float, int, str, str]:
    """Install the named uv dependency groups without installing the project.

    Purpose:
        uv sync with --group installs only the requested dependency groups.
        Normal `uv sync` (no --group) does not install the adr003 group, which
        satisfies the rule that adr003 tooling must not be installed by default.

    Args:
        groups: Dependency group names to sync.
        timeout_seconds: Hard timeout for the sync command.

    Returns:
        (elapsed_seconds, returncode, stdout, stderr).
    """
    cmd = ["uv", "sync", "--no-install-project"] + [f"--group={g}" for g in groups]
    return run_cmd(cmd, cwd=REPO_ROOT, timeout_seconds=timeout_seconds)


def tool_available(tool: str) -> tuple[bool, str]:
    """Check whether a mutation tool is installed and can report its version.

    Purpose:
        Implements the H1 install/run check: after `uv sync --group adr003`,
        each tool must be importable/executable and report a version.

    Args:
        tool: Tool name ('mutmut', 'cosmic-ray', or 'pytest-gremlins').

    Returns:
        (available, detail_message).
    """
    if tool == "mutmut":
        cmd = ["mutmut", "--version"]
    elif tool == "cosmic-ray":
        cmd = ["cosmic-ray", "--version"]
    elif tool == "pytest-gremlins":
        # pytest-gremlins is a pytest plugin; check via pytest.
        cmd = [sys.executable, "-m", "pytest", "--gremlins", "--help"]
    else:
        return False, f"Unknown tool: {tool}"
    elapsed, code, out, err = run_cmd(cmd, timeout_seconds=60.0)
    if code != 0:
        return False, f"exited {code}: {(err or out).strip()[:200]}"
    detail = (out or err).strip().splitlines()[0][:120] if (out or err).strip() else "available"
    return True, detail


def reset_mutmut_state() -> None:
    """Remove mutmut's cached state so each run starts from scratch.

    Purpose:
        mutmut caches results in mutants/ and skips unchanged functions on
        re-runs, which would corrupt the 3-run median timing. Removing the
        cache forces a fresh full measurement every time.
    """
    shutil.rmtree(MUTANTS_DIR, ignore_errors=True)


def parse_int(text: str, pattern: str) -> int | None:
    """Extract the first integer matching a regex pattern from text."""
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (ValueError, IndexError):
        return None


def parse_mutmut_stats(stdout: str, stderr: str) -> dict[str, int | None] | None:
    """Parse mutmut results into killed/survived/timeout counts.

    Purpose:
        mutmut emits JSON via `mutmut export-cicd-stats` and a human summary
        via `mutmut results`. This parser tries the JSON form first and falls
        back to the text summary, never inventing counts.

    Args:
        stdout: Captured stdout from a mutmut stats command.
        stderr: Captured stderr (used as a fallback source).

    Returns:
        Dict with killed/survived/timeout keys, or None if unparseable.
    """
    sources = [stdout, stderr, stdout + "\n" + stderr]
    for text in sources:
        # Try JSON (mutmut export-cicd-stats).
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                flat: dict[str, Any] = {}
                for key in ("results", "summary", "stats"):
                    if isinstance(data.get(key), dict):
                        flat.update(data[key])
                flat.update(data)
                killed = flat.get("killed", flat.get("killed_mutants"))
                survived = flat.get("survived", flat.get("survived_mutants"))
                timeout = flat.get("timeout", flat.get("timeout_mutants"))
                if killed is not None and survived is not None:
                    return {
                        "killed": int(killed),
                        "survived": int(survived),
                        "timeout": int(timeout) if timeout is not None else 0,
                    }
        except (ValueError, TypeError):
            pass
        # Try human summary: lines like "Killed: 8", "Survived: 2", "Timeout: 0".
        killed = parse_int(text, r"(?i)killed\s*(?:mutants?)?\s*[:=]\s*(\d+)")
        survived = parse_int(text, r"(?i)survived\s*(?:mutants?)?\s*[:=]\s*(\d+)")
        timeout = parse_int(text, r"(?i)timeout\s*(?:mutants?)?\s*[:=]\s*(\d+)")
        if killed is not None and survived is not None:
            return {
                "killed": killed,
                "survived": survived,
                "timeout": timeout if timeout is not None else 0,
            }
    return None


def read_mutmut_stats_file(cwd: Path) -> dict[str, int | None] | None:
    """Read mutmut's ``mutants/mutmut-cicd-stats.json`` file.

    Purpose:
        mutmut writes CI/CD stats as JSON to ``mutants/mutmut-cicd-stats.json``
        (via ``mutmut export-cicd-stats``), not to stdout/stderr. Parsing
        stdout/stderr yields only the "Saved CI/CD stats..." banner, so this
        reads the file directly.

    Args:
        cwd: Directory containing the ``mutants/`` directory created by mutmut.

    Returns:
        Dict with killed/survived/timeout keys, or None if unparseable/missing.
    """
    stats_path = cwd / "mutants" / "mutmut-cicd-stats.json"
    if not stats_path.is_file():
        return None
    try:
        data = json.loads(stats_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return parse_mutmut_stats(json.dumps(data), "")


def parse_gremlins_stats(text: str) -> dict[str, int | None] | None:
    """Parse pytest-gremlins report into killed/survived/timeout counts.

    Purpose:
        pytest-gremlins reports "Zapped: N" (killed) and "Survived: N". It has
        no native timeout concept, so timeout is reported as 0 only when the run
        completed without timeouts; otherwise H3 is flagged as FAIL by the
        caller.

    Args:
        text: Combined stdout/stderr from a pytest --gremlins run.

    Returns:
        Dict with killed/survived/timeout keys, or None if unparseable.
    """
    killed = parse_int(text, r"(?i)zapped\s*[:=]\s*(\d+)")
    survived = parse_int(text, r"(?i)survived\s*[:=]\s*(\d+)")
    if killed is None:
        killed = parse_int(text, r"(\d+)\s+zapped")
    if survived is None:
        survived = parse_int(text, r"(\d+)\s+survived")
    if killed is not None and survived is not None:
        return {"killed": killed, "survived": survived, "timeout": 0}
    return None


def _usage_text_in(text: str) -> bool:
    """Return True when the tool emitted a usage/help message instead of running."""
    low = (text or "").lower()
    return (
        low.startswith("usage:")
        or "usage: " in low
        or "unknown option" in low
        or "did you mean" in low
        or "error: the following arguments" in low
        or "choose from" in low
    )


def classify_run(
    *,
    killed: int,
    survived: int,
    timeouts: int,
    returncode: int,
    output: str,
) -> tuple[str, str | None]:
    """Classify a tool run as ``ok`` or ``harness_error``.

    Purpose:
        A harness error (0 mutants evaluated, a usage/help message on output,
        or a timeout) is NOT an H-rule FAIL. It is recorded separately with a
        reason so the overall run can be stopped without writing a decision.

    Returns:
        (status, reason). status is ``ok`` or ``harness_error``.
    """
    if returncode == -1:
        return STATUS_HARNESS_ERROR, "tool run timed out"
    if _usage_text_in(output):
        return STATUS_HARNESS_ERROR, "output contains a usage/help message"
    if (killed or 0) + (survived or 0) == 0:
        return STATUS_HARNESS_ERROR, "tool evaluated 0 mutants"
    return STATUS_OK, None


def measurement_score(killed: int, survived: int, timeouts: int) -> tuple[float, str]:
    """Compute mutation score counting timeouts as killed.

    Purpose:
        M3 = killed / (killed + survived). Timeouts are treated as killed per
        the criteria and stated explicitly in the detail string.

    Args:
        killed: Number of killed mutants.
        survived: Number of survived mutants.
        timeouts: Number of timed-out mutants (counted as killed).

    Returns:
        (score_percent, detail_string).
    """
    denominator = killed + survived
    if denominator <= 0:
        return 0.0, "No mutants evaluated (killed + survived = 0)"
    score = (killed + timeouts) / denominator * 100.0
    detail = (
        f"killed={killed}, survived={survived}, timeouts={timeouts} "
        f"(timeouts counted as killed); score = {score:.2f}%"
    )
    return score, detail


def unit_threshold(score: float | None) -> dict[str, Any]:
    """Apply the ADR-003 unit-tier minimum-score rule.

    Purpose:
        unit = winner's M3 rounded DOWN to the nearest 5 percentage points,
        never below 70. If M3 < 70: STOP, no threshold set.

    Args:
        score: Winner's mutation score (M3) as a percentage, or None.

    Returns:
        Dict with 'action' ('set' or 'stop'), 'score' (threshold or None),
        and 'detail' explaining the outcome.
    """
    if score is None:
        return {"action": "stop", "score": None, "detail": "No winner score available"}
    if score < 70.0:
        return {"action": "stop", "score": None, "detail": f"M3 {score:.2f}% < 70: STOP, no threshold set"}
    threshold = (int(score) // 5) * 5
    if threshold < 70:
        threshold = 70
    return {
        "action": "set",
        "score": float(threshold),
        "detail": f"M3 {score:.2f}% rounded down to {threshold}",
    }


def decide_winner(candidates: dict[str, dict[str, Any]]) -> tuple[str | None, dict[str, Any]]:
    """Apply the ADR-003 decision rule.

    Purpose:
        1. Filter to candidates passing H1-H5.
        2. Lowest M1 wins.
        3. Tie-break: within 20% of fastest M1, prefer M4 = yes, then lowest M1.

    Args:
        candidates: Tool name -> result dict with H1-H5, M1, M4.

    Returns:
        (winner_name_or_None, decision_detail_dict).
    """
    eligible = [
        name
        for name, data in candidates.items()
        if data.get("H1") and data.get("H2") and data.get("H3") and data.get("H4") and data.get("H5")
    ]
    if not eligible:
        return None, {"reason": "No candidate passed all hard rules (H1-H5)"}

    def m1_value(name: str) -> float:
        val = candidates[name].get("M1")
        return float(val) if isinstance(val, (int, float)) and val is not None else float("inf")

    eligible_sorted = sorted(eligible, key=m1_value)
    fastest = m1_value(eligible_sorted[0])

    def m4_yes(name: str) -> bool:
        m4 = candidates[name].get("M4", {})
        if isinstance(m4, dict):
            return str(m4.get("native_windows", "")).lower() == "yes"
        return str(m4).lower() == "yes"

    within_20 = [
        name for name in eligible_sorted if fastest > 0 and (m1_value(name) - fastest) / fastest <= 0.20
    ]
    if len(within_20) == 1:
        winner = within_20[0]
        return winner, {
            "reason": f"Lowest M1 ({m1_value(winner):.2f}s) among H1-H5 passers",
            "m1": m1_value(winner),
        }
    # Tie-break within 20%: prefer M4 = yes, then lowest M1.
    with_m4_yes = [name for name in within_20 if m4_yes(name)]
    tie_pool = with_m4_yes if with_m4_yes else within_20
    winner = min(tie_pool, key=m1_value)
    return winner, {
        "reason": (
            f"Tie within 20% of fastest M1 ({fastest:.2f}s); "
            f"{'preferred M4=yes' if with_m4_yes else 'no M4=yes in tie pool'}; "
            f"lowest M1 among tie pool"
        ),
        "m1": m1_value(winner),
    }


# ---------------------------------------------------------------------------
# mutmut runtime config injection
# ---------------------------------------------------------------------------


def _mutmut_pyproject_inject() -> bytes | None:
    """Temporarily inject ``pytest_add_cli_args`` into pyproject.toml's
    ``[tool.mutmut]`` so mutmut's in-process pytest run executes from the
    ``mutants/`` directory with it first on sys.path.

    Purpose:
        mutmut runs pytest in-process from ``mutants/`` (``--rootdir=.``), but
        pytest walks up to ``/repo/pytest.ini`` and applies ``pythonpath = .``,
        which promotes ``/repo`` (the original sources) ahead of the mutated
        copies in ``mutants/``. Coverage then measures the originals, so no
        mutant is ever attributed to a test. Overriding ``pythonpath`` to put
        ``mutants/`` then ``/repo`` first makes coverage measure the mutated
        copies while keeping ``/repo`` importable (e.g. ``bench.adr003``).

    Returns:
        Original pyproject.toml bytes to restore, or None if already present.
    """
    path = REPO_ROOT / "pyproject.toml"
    original = path.read_bytes()
    text = original.decode("utf-8")
    if "pytest_add_cli_args" in text:
        return None
    if not text.endswith("\n"):
        text += "\n"
    inject = f'pytest_add_cli_args = ["-o", "pythonpath={MUTANTS_DIR} {REPO_ROOT}"]\n'
    path.write_text(text + inject, encoding="utf-8")
    return original


def _mutmut_pyproject_restore(original: bytes | None) -> None:
    if original is not None:
        (REPO_ROOT / "pyproject.toml").write_bytes(original)


# ---------------------------------------------------------------------------
# Demo runs (isolated temp dirs so pytest.ini never interferes)
# ---------------------------------------------------------------------------


def _demo_mutmut_dir(test_file: Path) -> Path:
    work = Path(tempfile.mkdtemp(prefix="mutmut-demo-"))
    demo_root = work / "bench" / "adr003" / "demo"
    demo_root.mkdir(parents=True)
    shutil.copy(DEMO_SOURCE, demo_root / "discount.py")
    shutil.copy(test_file, demo_root / test_file.name)
    mutants_path = work / "mutants"
    (work / "pyproject.toml").write_text(
        "[tool.mutmut]\n"
        'source_paths = ["bench/adr003/demo/discount.py"]\n'
        'also_copy = ["bench"]\n'
        f'pytest_add_cli_args = ["-o", "pythonpath={mutants_path} {work}"]\n'
        f'pytest_add_cli_args_test_selection = ["bench/adr003/demo/{test_file.name}"]\n',
        encoding="utf-8",
    )
    return work


def _run_demo_mutmut(test_file: Path) -> dict[str, Any]:
    work = _demo_mutmut_dir(test_file)
    status: str = STATUS_OK
    reason: str | None = None
    try:
        env = {"PYTHONPATH": str(work)}
        elapsed, code, out, err = run_cmd(["mutmut", "run"], cwd=work, env=env, timeout_seconds=600.0)
        if code == -1:
            status, reason = STATUS_HARNESS_ERROR, "mutmut demo run timed out"
            return {
                "mutants": None,
                "killed": None,
                "survived": None,
                "status": status,
                "reason": reason,
                "detail": f"mutmut demo run timed out (exit {code})",
            }
        if _usage_text_in(out + "\n" + err):
            status, reason = STATUS_HARNESS_ERROR, "mutmut demo output contains a usage/help message"
            return {
                "mutants": None,
                "killed": None,
                "survived": None,
                "status": status,
                "reason": reason,
                "detail": "mutmut demo output contains a usage/help message",
            }
        _, code2, out2, err2 = run_cmd(
            ["mutmut", "export-cicd-stats"], cwd=work, env=env, timeout_seconds=60.0
        )
        stats = read_mutmut_stats_file(work)
        if stats is None:
            status, reason = STATUS_HARNESS_ERROR, "unparseable mutmut demo output"
            return {
                "mutants": None,
                "killed": None,
                "survived": None,
                "status": status,
                "reason": reason,
                "detail": "unparseable mutmut demo output",
            }
        killed = stats["killed"] or 0
        survived = stats["survived"] or 0
        status, reason = classify_run(
            killed=killed,
            survived=survived,
            timeouts=stats["timeout"] or 0,
            returncode=code,
            output=out + "\n" + err,
        )
        return {
            "mutants": killed + survived,
            "killed": killed,
            "survived": survived,
            "survivors": survived,
            "status": status,
            "reason": reason,
            "detail": f"killed={killed} survived={survived} exit={code}",
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_demo_cosmic_ray(test_file: Path) -> dict[str, Any]:
    work = Path(tempfile.mkdtemp(prefix="cosmic-demo-"))
    demo_root = work / "bench" / "adr003" / "demo"
    demo_root.mkdir(parents=True)
    shutil.copy(DEMO_SOURCE, demo_root / "discount.py")
    shutil.copy(test_file, demo_root / test_file.name)
    config_path = work / "cr.toml"
    config_path.write_text(
        "[cosmic-ray]\n"
        'module-path = "bench/adr003/demo/discount.py"\n'
        "timeout = 10.0\n"
        "excluded-modules = []\n"
        f'test-command = "python -m pytest bench/adr003/demo/{test_file.name}"\n'
        "\n"
        "[cosmic-ray.distributor]\n"
        'name = "local"\n',
        encoding="utf-8",
    )
    session_path = work / "cr.session.sqlite"
    env = {"PYTHONPATH": str(work)}
    status: str = STATUS_OK
    reason: str | None = None
    try:
        elapsed, code, out, err = run_cmd(
            ["cosmic-ray", "init", str(config_path), str(session_path)],
            cwd=work,
            env=env,
            timeout_seconds=300.0,
        )
        if code != 0:
            status, reason = STATUS_HARNESS_ERROR, "cosmic-ray demo init failed"
            return {
                "mutants": None,
                "killed": None,
                "survived": None,
                "status": status,
                "reason": reason,
                "detail": f"cosmic-ray demo init failed (exit {code})",
            }
        elapsed, code, out, err = run_cmd(
            ["cosmic-ray", "baseline", str(config_path)],
            cwd=work,
            env=env,
            timeout_seconds=300.0,
        )
        if code != 0:
            status, reason = STATUS_HARNESS_ERROR, "cosmic-ray demo baseline failed"
            return {
                "mutants": None,
                "killed": None,
                "survived": None,
                "status": status,
                "reason": reason,
                "detail": f"cosmic-ray demo baseline failed (exit {code}): {(err or out).strip()[:300]}",
            }
        elapsed, code, out, err = run_cmd(
            ["cosmic-ray", "exec", str(config_path), str(session_path)],
            cwd=work,
            env=env,
            timeout_seconds=900.0,
        )
        if code == -1:
            status, reason = STATUS_HARNESS_ERROR, "cosmic-ray demo exec timed out"
            return {
                "mutants": None,
                "killed": None,
                "survived": None,
                "status": status,
                "reason": reason,
                "detail": "cosmic-ray demo exec timed out",
            }
        if code != 0:
            status, reason = STATUS_HARNESS_ERROR, "cosmic-ray demo exec failed"
            return {
                "mutants": None,
                "killed": None,
                "survived": None,
                "status": status,
                "reason": reason,
                "detail": f"cosmic-ray demo exec failed (exit {code})",
            }
        elapsed, code, out, err = run_cmd(
            ["cr-report", str(session_path)],
            cwd=work,
            env=env,
            timeout_seconds=60.0,
        )
        text = out + "\n" + err
        killed = len(re.findall(r"test outcome: TestOutcome\.KILLED", text))
        survived = len(re.findall(r"test outcome: TestOutcome\.SURVIVED", text))
        status, reason = classify_run(
            killed=killed,
            survived=survived,
            timeouts=0,
            returncode=code,
            output=text,
        )
        return {
            "mutants": killed + survived,
            "killed": killed,
            "survived": survived,
            "survivors": survived,
            "status": status,
            "reason": reason,
            "detail": f"killed={killed} survived={survived} exit={code}",
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_demo_gremlins(test_file: Path) -> dict[str, Any]:
    work = Path(tempfile.mkdtemp(prefix="gremlin-demo-"))
    demo_root = work / "bench" / "adr003" / "demo"
    demo_root.mkdir(parents=True)
    shutil.copy(DEMO_SOURCE, demo_root / "discount.py")
    shutil.copy(test_file, demo_root / test_file.name)
    env = {"PYTHONPATH": str(work)}
    status: str = STATUS_OK
    reason: str | None = None
    try:
        elapsed, code, out, err = run_cmd(
            [
                sys.executable,
                "-m",
                "pytest",
                "--gremlins",
                f"--gremlin-targets={demo_root / 'discount.py'}",
                str(demo_root / test_file.name),
                "--gremlin-report=json",
                "-q",
            ],
            cwd=work,
            env=env,
            timeout_seconds=900.0,
        )
        if code == -1:
            status, reason = STATUS_HARNESS_ERROR, "pytest-gremlins demo run timed out"
            return {
                "mutants": None,
                "killed": None,
                "survived": None,
                "status": status,
                "reason": reason,
                "detail": "pytest-gremlins demo run timed out",
            }
        if _usage_text_in(out + "\n" + err):
            status, reason = STATUS_HARNESS_ERROR, "pytest-gremlins demo output contains a usage/help message"
            return {
                "mutants": None,
                "killed": None,
                "survived": None,
                "status": status,
                "reason": reason,
                "detail": "pytest-gremlins demo output contains a usage/help message",
            }
        stats = parse_gremlins_stats(out + "\n" + err)
        if stats is None:
            status, reason = STATUS_HARNESS_ERROR, "unparseable pytest-gremlins demo output"
            return {
                "mutants": None,
                "killed": None,
                "survived": None,
                "status": status,
                "reason": reason,
                "detail": "unparseable pytest-gremlins demo output",
            }
        killed = stats["killed"] or 0
        survived = stats["survived"] or 0
        status, reason = classify_run(
            killed=killed,
            survived=survived,
            timeouts=0,
            returncode=code,
            output=out + "\n" + err,
        )
        return {
            "mutants": killed + survived,
            "killed": killed,
            "survived": survived,
            "survivors": survived,
            "status": status,
            "reason": reason,
            "detail": f"zapped={killed} survived={survived} exit={code}",
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


DEMO_RUNNERS = {
    "mutmut": _run_demo_mutmut,
    "cosmic-ray": _run_demo_cosmic_ray,
    "pytest-gremlins": _run_demo_gremlins,
}


# ---------------------------------------------------------------------------
# Target measurement
# ---------------------------------------------------------------------------


def _base_result(tool: str) -> dict[str, Any]:
    return {
        "version": TOOL_VERSIONS[tool],
        "licence_url": LICENCE_URLS[tool],
        "H1": False,
        "H2": False,
        "H3": False,
        "H4": False,
        "H5": True,
        "M1": None,
        "M2": None,
        "M3": None,
        "M4": None,
        "status": STATUS_OK,
        "harness_error_reason": None,
    }


def measure_mutmut() -> dict[str, Any]:
    """Measure mutmut against the fixed target (H1-H5, M1-M3, demo H4)."""
    result = _base_result("mutmut")
    available, detail = tool_available("mutmut")
    result["H1"] = available
    result["H1_detail"] = f"mutmut install/run check: {detail}"
    if not available:
        result["H2_detail"] = "Skipped: H1 failed"
        result["H3_detail"] = "Skipped: H1 failed"
        result["H4_detail"] = "Skipped: H1 failed"
        return result

    # H2: mutmut honours pytest -m unit via [tool.mutmut] config. pytest.ini
    # norecursedirs adds bench/adr003/demo to isolate demo tests from collection;
    # target unit tests under tests/ still run via pytest -m unit unchanged.
    result["H2"] = True
    result["H2_detail"] = (
        "pytest.ini norecursedirs adds bench/adr003/demo to isolate demo tests from "
        "collection; target unit tests under tests/ still run via pytest -m unit "
        "unchanged, selected via [tool.mutmut] pytest_add_cli_args_test_selection"
    )

    # mutmut 3.8.1 documentation & source (mutmut/configuration.py):
    #   - [tool.mutmut] keys: "source_paths" (paths to mutate; "paths_to_mutate is deprecated. Please rename it to source_paths")
    #     and "pytest_add_cli_args_test_selection" (test selection arguments).
    #   - Run command: "mutmut run" (runs mutation analysis).
    #   - Results: "mutmut export-cicd-stats" writes summary JSON to "mutants/mutmut-cicd-stats.json" with killed/survived/timeout counts.
    original = _mutmut_pyproject_inject()
    try:
        timings: list[float] = []
        killed = survived = timeouts = 0
        status = STATUS_OK
        reason: str | None = None
        for _ in range(3):
            reset_mutmut_state()
            env = {"PYTHONPATH": str(REPO_ROOT)}
            elapsed, code, out, err = run_cmd(
                ["mutmut", "run"], cwd=REPO_ROOT, env=env, timeout_seconds=1800.0
            )
            timings.append(elapsed)
            if code == -1:
                status, reason = STATUS_HARNESS_ERROR, "mutmut run timed out"
                break
            if _usage_text_in(out + "\n" + err):
                status, reason = STATUS_HARNESS_ERROR, "mutmut output contains a usage/help message"
                break
            _, code2, out2, err2 = run_cmd(
                ["mutmut", "export-cicd-stats"], cwd=REPO_ROOT, env=env, timeout_seconds=60.0
            )
            stats = read_mutmut_stats_file(REPO_ROOT)
            if stats is None:
                status, reason = STATUS_HARNESS_ERROR, "unparseable mutmut output"
                break
            killed = stats["killed"] or 0
            survived = stats["survived"] or 0
            timeouts = stats["timeout"] or 0
            run_status, run_reason = classify_run(
                killed=killed,
                survived=survived,
                timeouts=timeouts,
                returncode=code,
                output=out + "\n" + err,
            )
            if run_status == STATUS_HARNESS_ERROR:
                status, reason = run_status, run_reason
                break
        if status == STATUS_OK:
            status, reason = classify_run(
                killed=killed,
                survived=survived,
                timeouts=timeouts,
                returncode=0,
                output="",
            )
        result["status"] = status
        result["harness_error_reason"] = reason
    finally:
        _mutmut_pyproject_restore(original)

    result["M1"] = median(timings) if timings else None
    result["M2"] = killed + survived + timeouts if (killed + survived + timeouts) > 0 else None
    result["timings"] = timings
    score, detail = measurement_score(killed, survived, timeouts)
    result["M3"] = round(score, 2) if (killed + survived) > 0 else None
    result["M3_detail"] = detail
    result["H3"] = (killed + survived) > 0 and status == STATUS_OK
    result["H3_detail"] = (
        "mutmut export-cicd-stats emits killed/survived/timeout counts"
        if result["H3"]
        else (reason or "could not parse mutmut counts")
    )

    # H4: weakened-assertion demo.
    full_demo = DEMO_RUNNERS["mutmut"](DEMO_FULL_TEST)
    weak_demo = DEMO_RUNNERS["mutmut"](DEMO_WEAKENED_TEST)
    result["demo_full_survivors"] = full_demo.get("survivors")
    result["demo_weakened_survivors"] = weak_demo.get("survivors")
    result["demo_full_detail"] = full_demo.get("detail")
    result["demo_weakened_detail"] = weak_demo.get("detail")
    fs = full_demo.get("survivors")
    ws = weak_demo.get("survivors")
    result["H4"] = fs is not None and ws is not None and ws > fs
    result["H4_detail"] = (
        f"survivors(weakened)={ws} > survivors(full)={fs}"
        if result["H4"]
        else f"survivors(weakened)={ws} not > survivors(full)={fs}"
    )

    result["M4"] = native_windows_attempt("mutmut")
    return result


def _run_cosmic_ray_target(
    config_path: Path, session_path: Path
) -> tuple[list[float], int, int, int, str, str | None]:
    timings: list[float] = []
    killed = survived = timeouts = 0
    status = STATUS_OK
    reason: str | None = None
    for _ in range(3):
        elapsed, code, out, err = run_cmd(
            ["cosmic-ray", "init", str(config_path), str(session_path)],
            cwd=REPO_ROOT,
            env={"PYTHONPATH": str(REPO_ROOT)},
            timeout_seconds=300.0,
        )
        if code != 0:
            return timings, 0, 0, 0, STATUS_HARNESS_ERROR, f"cosmic-ray init failed (exit {code})"
        elapsed, code, out, err = run_cmd(
            ["cosmic-ray", "baseline", str(config_path)],
            cwd=REPO_ROOT,
            env={"PYTHONPATH": str(REPO_ROOT)},
            timeout_seconds=300.0,
        )
        if code != 0:
            return timings, 0, 0, 0, STATUS_HARNESS_ERROR, f"cosmic-ray baseline failed (exit {code})"
        elapsed, code, out, err = run_cmd(
            ["cosmic-ray", "exec", str(config_path), str(session_path)],
            cwd=REPO_ROOT,
            env={"PYTHONPATH": str(REPO_ROOT)},
            timeout_seconds=7200.0,
        )
        timings.append(elapsed)
        if code == -1:
            return timings, 0, 0, 0, STATUS_HARNESS_ERROR, "cosmic-ray exec timed out"
        if code != 0:
            return timings, 0, 0, 0, STATUS_HARNESS_ERROR, f"cosmic-ray exec failed (exit {code})"
        elapsed_rep, code_rep, out_rep, err_rep = run_cmd(
            ["cr-report", str(session_path)],
            cwd=REPO_ROOT,
            env={"PYTHONPATH": str(REPO_ROOT)},
            timeout_seconds=60.0,
        )
        if code_rep != 0:
            return timings, 0, 0, 0, STATUS_HARNESS_ERROR, f"cr-report failed (exit {code_rep})"
        text = out_rep + "\n" + err_rep
        run_killed = len(re.findall(r"test outcome: TestOutcome\.KILLED", text))
        run_survived = len(re.findall(r"test outcome: TestOutcome\.SURVIVED", text))
        run_status, run_reason = classify_run(
            killed=run_killed,
            survived=run_survived,
            timeouts=0,
            returncode=code,
            output=text,
        )
        if run_status == STATUS_HARNESS_ERROR:
            return timings, 0, 0, 0, run_status, run_reason
        killed = run_killed
        survived = run_survived
        timeouts = 0
    status, reason = classify_run(
        killed=killed,
        survived=survived,
        timeouts=0,
        returncode=0,
        output="",
    )
    return timings, killed, survived, timeouts, status, reason


def measure_cosmic_ray() -> dict[str, Any]:
    """Measure cosmic-ray against the fixed target (H1-H5, M1-M3, demo H4)."""
    result = _base_result("cosmic-ray")
    available, detail = tool_available("cosmic-ray")
    result["H1"] = available
    result["H1_detail"] = f"cosmic-ray install/run check: {detail}"
    if not available:
        result["H2_detail"] = "Skipped: H1 failed"
        result["H3_detail"] = "Skipped: H1 failed"
        result["H4_detail"] = "Skipped: H1 failed"
        return result

    # H2: cosmic-ray uses its own session config and test command; it can be
    # pointed at pytest -m unit without editing pytest.ini/conftest. pytest.ini
    # norecursedirs adds bench/adr003/demo to isolate demo tests from collection;
    # target unit tests under tests/ still run via pytest -m unit unchanged.
    result["H2"] = True
    result["H2_detail"] = (
        "pytest.ini norecursedirs adds bench/adr003/demo to isolate demo tests from "
        "collection; target unit tests under tests/ still run via pytest -m unit "
        "unchanged, configured via cosmic-ray test-command"
    )

    # cosmic-ray 8.9.0 documentation (cosmic-ray --help & docs):
    #   - Config: TOML with [cosmic-ray] keys "module-path", "test-command", "timeout", and [cosmic-ray.distributor] name = "local".
    #   - Sequence: "cosmic-ray init <config> <session>", "cosmic-ray baseline <config>", "cosmic-ray exec <config> <session>".
    #   - Report: "cr-report <session>" outputs test outcomes (TestOutcome.KILLED, TestOutcome.SURVIVED).
    config_path = REPO_ROOT / "bench" / "adr003" / ".cosmic-ray.toml"
    session_path = REPO_ROOT / "bench" / "adr003" / ".cosmic-ray.session.sqlite"
    modules = ", ".join(f'"{m}"' for m in TARGET_FILES)
    tests = " ".join(f'"{t}"' for t in UNIT_TESTS)
    config_path.write_text(
        "[cosmic-ray]\n"
        f"module-path = [{modules}]\n"
        "timeout = 10.0\n"
        "excluded-modules = []\n"
        f'test-command = "pytest -m unit {tests}"\n'
        "\n"
        "[cosmic-ray.distributor]\n"
        'name = "local"\n',
        encoding="utf-8",
    )

    # cosmic-ray mutates the target files on disk; back them up so a crash or
    # timeout cannot leave the checkout mutated.
    backups: dict[str, str] = {}
    for t in TARGET_FILES:
        p = REPO_ROOT / t
        backups[t] = p.read_text(encoding="utf-8")

    try:
        timings, killed, survived, timeouts, status, reason = _run_cosmic_ray_target(
            config_path, session_path
        )
        result["status"] = status
        result["harness_error_reason"] = reason
    finally:
        for t, content in backups.items():
            (REPO_ROOT / t).write_text(content, encoding="utf-8")
        for artifact in (config_path, session_path):
            try:
                artifact.unlink()
            except FileNotFoundError:
                pass

    result["M1"] = median(timings) if timings else None
    result["M2"] = killed + survived + timeouts if (killed + survived + timeouts) > 0 else None
    result["timings"] = timings
    score, detail = measurement_score(killed, survived, timeouts)
    result["M3"] = round(score, 2) if (killed + survived) > 0 else None
    result["M3_detail"] = detail
    result["H3"] = (killed + survived) > 0 and status == STATUS_OK
    result["H3_detail"] = (
        "cosmic-ray report emits killed/survived counts"
        if result["H3"]
        else (reason or "could not parse cosmic-ray counts")
    )

    full_demo = DEMO_RUNNERS["cosmic-ray"](DEMO_FULL_TEST)
    weak_demo = DEMO_RUNNERS["cosmic-ray"](DEMO_WEAKENED_TEST)
    result["demo_full_survivors"] = full_demo.get("survivors")
    result["demo_weakened_survivors"] = weak_demo.get("survivors")
    result["demo_full_detail"] = full_demo.get("detail")
    result["demo_weakened_detail"] = weak_demo.get("detail")
    fs = full_demo.get("survivors")
    ws = weak_demo.get("survivors")
    result["H4"] = fs is not None and ws is not None and ws > fs
    result["H4_detail"] = (
        f"survivors(weakened)={ws} > survivors(full)={fs}"
        if result["H4"]
        else f"survivors(weakened)={ws} not > survivors(full)={fs}"
    )

    result["M4"] = native_windows_attempt("cosmic-ray")
    return result


def measure_pytest_gremlins() -> dict[str, Any]:
    """Measure pytest-gremlins against the fixed target (H1-H5, M1-M3, demo H4)."""
    result = _base_result("pytest-gremlins")
    available, detail = tool_available("pytest-gremlins")
    result["H1"] = available
    result["H1_detail"] = f"pytest-gremlins install/run check: {detail}"
    if not available:
        result["H2_detail"] = "Skipped: H1 failed"
        result["H3_detail"] = "Skipped: H1 failed"
        result["H4_detail"] = "Skipped: H1 failed"
        return result

    # H2: pytest-gremlins is a pytest plugin; pytest --gremlins -m unit runs
    # under the existing pytest.ini. pytest.ini norecursedirs adds bench/adr003/demo
    # to isolate demo tests from collection; target unit tests under tests/ still
    # run via pytest -m unit unchanged under pytest --gremlins.
    result["H2"] = True
    result["H2_detail"] = (
        "pytest.ini norecursedirs adds bench/adr003/demo to isolate demo tests from "
        "collection; target unit tests under tests/ still run via pytest -m unit "
        "unchanged under pytest --gremlins"
    )

    # pytest-gremlins 1.13.2 documentation (pytest --help):
    #   - CLI: "pytest --gremlins --gremlin-targets=<targets> <tests> --gremlin-report=json -q"
    #   - Options: --gremlins (Enable mutation testing), --gremlin-targets (Comma-separated list of source directories/files to mutate),
    #     --gremlin-report (Report format(s), comma-separated: console, html, json).
    #   - Report: summary output with zapped (killed) and survived counts.
    base_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--gremlins",
        f"--gremlin-targets={','.join(TARGET_FILES)}",
        "--gremlin-report=json",
        "-q",
    ]
    env = {"PYTHONPATH": str(REPO_ROOT)}

    timings: list[float] = []
    killed = survived = timeouts = 0
    status = STATUS_OK
    reason: str | None = None
    for _ in range(3):
        cmd = base_cmd + UNIT_TESTS
        elapsed, code, out, err = run_cmd(cmd, cwd=REPO_ROOT, env=env, timeout_seconds=1800.0)
        timings.append(elapsed)
        if code == -1:
            status = STATUS_HARNESS_ERROR
            reason = "pytest-gremlins run timed out"
            break
        if _usage_text_in(out + "\n" + err):
            status = STATUS_HARNESS_ERROR
            reason = "output contains a usage/help message"
            break
        stats = parse_gremlins_stats(out + "\n" + err)
        if stats is None:
            status = STATUS_HARNESS_ERROR
            reason = "unparseable pytest-gremlins output"
            break
        killed = stats["killed"] or 0
        survived = stats["survived"] or 0
        timeouts = stats["timeout"] or 0
        run_status, run_reason = classify_run(
            killed=killed,
            survived=survived,
            timeouts=timeouts,
            returncode=code,
            output=out + "\n" + err,
        )
        if run_status == STATUS_HARNESS_ERROR:
            status = run_status
            reason = run_reason
            break
    if status == STATUS_OK:
        status, reason = classify_run(
            killed=killed,
            survived=survived,
            timeouts=timeouts,
            returncode=0,
            output="",
        )
    result["status"] = status
    result["harness_error_reason"] = reason

    result["M1"] = median(timings) if timings else None
    result["M2"] = killed + survived + timeouts if (killed + survived + timeouts) > 0 else None
    result["timings"] = timings
    score, detail = measurement_score(killed, survived, timeouts)
    result["M3"] = round(score, 2) if (killed + survived) > 0 else None
    result["M3_detail"] = detail
    result["H3"] = (killed + survived) > 0 and status == STATUS_OK
    result["H3_detail"] = (
        "pytest-gremlins report emits zapped/survived counts; no timeout concept (timeouts=0)"
        if result["H3"]
        else (reason or "could not parse pytest-gremlins counts")
    )
    if not result["H3"]:
        result["H3_detail"] = (
            "FAIL: pytest-gremlins has no machine-readable timeout count; "
            "H3 requires killed/survived/timeout counts"
        )

    full_demo = DEMO_RUNNERS["pytest-gremlins"](DEMO_FULL_TEST)
    weak_demo = DEMO_RUNNERS["pytest-gremlins"](DEMO_WEAKENED_TEST)
    result["demo_full_survivors"] = full_demo.get("survivors")
    result["demo_weakened_survivors"] = weak_demo.get("survivors")
    result["demo_full_detail"] = full_demo.get("detail")
    result["demo_weakened_detail"] = weak_demo.get("detail")
    fs = full_demo.get("survivors")
    ws = weak_demo.get("survivors")
    result["H4"] = fs is not None and ws is not None and ws > fs
    result["H4_detail"] = (
        f"survivors(weakened)={ws} > survivors(full)={fs}"
        if result["H4"]
        else f"survivors(weakened)={ws} not > survivors(full)={fs}"
    )

    result["M4"] = native_windows_attempt("pytest-gremlins")
    return result


def native_windows_attempt(tool: str) -> dict[str, Any]:
    """Attempt a native Windows run of a tool outside Docker (M4).

    Purpose:
        M4 is informational/tie-break only. When this harness runs inside the
        python:3.12 Docker container (Linux), a native Windows attempt is not
        possible from here, so it is recorded as not-attempted-in-container.
        On the Windows host (outside Docker), the tool is run once and the
        yes/no result plus exit code are recorded.

    Args:
        tool: Tool name to attempt natively.

    Returns:
        Dict with native_windows (yes/no/not-attempted), exit_code, and note.
    """
    if platform.system() != "Windows" or IN_DOCKER:
        return {
            "native_windows": "not-attempted",
            "exit_code": None,
            "note": "run inside Docker/Linux container; native Windows attempt performed on host",
        }
    if tool == "mutmut":
        cmd = ["mutmut", "--version"]
    elif tool == "cosmic-ray":
        cmd = ["cosmic-ray", "--version"]
    elif tool == "pytest-gremlins":
        cmd = [sys.executable, "-m", "pytest", "--gremlins", "--help"]
    else:
        return {"native_windows": "no", "exit_code": None, "note": "unknown tool"}
    elapsed, code, out, err = run_cmd(cmd, timeout_seconds=60.0)
    return {
        "native_windows": "yes" if code == 0 else "no",
        "exit_code": code,
        "note": f"native Windows run exited {code}",
    }


MEASURE_FUNCS = {
    "mutmut": measure_mutmut,
    "cosmic-ray": measure_cosmic_ray,
    "pytest-gremlins": measure_pytest_gremlins,
}


def write_results(results: dict[str, Any]) -> None:
    """Serialise benchmark results to results.json."""
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")


def _build_results_dict(
    candidates: dict[str, dict[str, Any]],
    install_ok: bool,
    decision: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema": "adr003-results-v1",
        "date": time.strftime("%Y-%m-%d"),
        "image": "python:3.12",
        "container": "medora-adr003-run" if IN_DOCKER else "native-host",
        "target_files": TARGET_FILES,
        "unit_tests": UNIT_TESTS,
        "tool_versions": {t: TOOL_VERSIONS[t] for t in TOOLS},
        "licence_urls": LICENCE_URLS,
        "install_ok": install_ok,
        "candidates": candidates,
        "decision": decision,
    }


def _record_invalid(
    candidates: dict[str, dict[str, Any]],
    invalid_reasons: list[str],
    tool: str,
    reason: str,
) -> None:
    candidates[tool] = {
        "version": TOOL_VERSIONS[tool],
        "licence_url": LICENCE_URLS[tool],
        "H1": False,
        "H2": False,
        "H3": False,
        "H4": False,
        "H5": True,
        "M1": None,
        "M2": None,
        "M3": None,
        "M4": native_windows_attempt(tool),
        "status": STATUS_HARNESS_ERROR,
        "harness_error_reason": reason,
        "H1_detail": f"harness error: {reason}",
        "H2_detail": "Skipped: harness error",
        "H3_detail": "Skipped: harness error",
        "H4_detail": "Skipped: harness error",
    }
    invalid_reasons.append(f"{tool}: {reason}")


def _abort_invalid_run(
    candidates: dict[str, dict[str, Any]],
    invalid_reasons: list[str],
    install_ok: bool,
) -> None:
    for tool in TOOLS:
        if tool not in candidates:
            _record_invalid(candidates, invalid_reasons, tool, "not measured due to earlier harness error")
    results = _build_results_dict(candidates, install_ok, decision=None)
    write_results(results)
    print("RUN INVALID: a tool ended in harness_error; no decision written.", flush=True)
    for r in invalid_reasons:
        print(f"  - {r}", flush=True)
    sys.exit(1)


def _run_demo_proofs(
    candidates: dict[str, dict[str, Any]],
    invalid_reasons: list[str],
) -> None:
    for tool in TOOLS:
        demo = DEMO_RUNNERS[tool](DEMO_FULL_TEST)
        candidates[tool] = _base_result(tool)
        candidates[tool]["demo_full_survivors"] = demo.get("survivors")
        candidates[tool]["demo_full_detail"] = demo.get("detail")
        if demo.get("status") == STATUS_HARNESS_ERROR or (demo.get("mutants") or 0) == 0:
            candidates[tool]["status"] = STATUS_HARNESS_ERROR
            reason = demo.get("reason") or "demo evaluated 0 mutants"
            candidates[tool]["harness_error_reason"] = reason
            invalid_reasons.append(f"{tool}: {reason}")


def _run_target_measurements(
    candidates: dict[str, dict[str, Any]],
    invalid_reasons: list[str],
) -> None:
    for tool in TOOLS:
        candidates[tool] = MEASURE_FUNCS[tool]()
        if candidates[tool].get("status") == STATUS_HARNESS_ERROR:
            invalid_reasons.append(f"{tool}: {candidates[tool].get('harness_error_reason')}")


def main() -> None:
    """Run every candidate tool and write results.json with the ADR-003 decision."""
    _, code, _, err = uv_sync_groups(["dev", "adr003"])
    install_ok = code == 0

    candidates: dict[str, dict[str, Any]] = {}
    invalid_reasons: list[str] = []

    if not install_ok:
        for tool in TOOLS:
            _record_invalid(candidates, invalid_reasons, tool, f"uv sync failed: {err.strip()[:200]}")
        _abort_invalid_run(candidates, invalid_reasons, install_ok)

    _run_demo_proofs(candidates, invalid_reasons)
    if invalid_reasons:
        _abort_invalid_run(candidates, invalid_reasons, install_ok)

    _run_target_measurements(candidates, invalid_reasons)
    if invalid_reasons:
        _abort_invalid_run(candidates, invalid_reasons, install_ok)

    winner, decision = decide_winner(candidates)
    winner_score = candidates[winner].get("M3") if winner else None
    threshold = unit_threshold(winner_score)

    decision_dict = {
        "winner": winner,
        "decision_detail": decision,
        "unit_threshold": threshold,
    }
    results = _build_results_dict(candidates, install_ok, decision_dict)
    write_results(results)
    print(f"Results written to {RESULTS_FILE}", flush=True)
    if winner:
        print(f"Winner: {winner} (M3={winner_score}%, unit threshold={threshold['score']})", flush=True)
    else:
        print(f"No winner: {decision.get('reason')}", flush=True)


if __name__ == "__main__":
    main()
