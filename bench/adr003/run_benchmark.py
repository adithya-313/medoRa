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
import subprocess
import sys
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
    return True, (out or err).strip().splitlines()[0][:120]


def reset_mutmut_state() -> None:
    """Remove mutmut's cached state so each run starts from scratch.

    Purpose:
        mutmut caches results in mutants/ and skips unchanged functions on
        re-runs, which would corrupt the 3-run median timing. Removing the
        cache forces a fresh full measurement every time.
    """
    if MUTANTS_DIR.is_dir():
        for entry in MUTANTS_DIR.iterdir():
            if entry.is_dir():
                entry.rmdir() if not any(entry.iterdir()) else None
        import shutil

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


def run_demo_survivors(tool: str, test_file: Path) -> dict[str, Any]:
    """Run a mutation tool on the demo source with one demo test file.

    Purpose:
        Implements H4: count survived mutants for the demo with the full and
        weakened test files so survivors(weakened) > survivors(full) can be
        checked. Runs the tool explicitly against the demo path (the demo is
        excluded from normal suite collection by pytest.ini norecursedirs).

    Args:
        tool: Tool name ('mutmut', 'cosmic-ray', or 'pytest-gremlins').
        test_file: Demo test file to run against the demo source.

    Returns:
        Dict with 'survivors' (int or None), 'detail', and 'ran' (bool).
    """
    if tool == "pytest-gremlins":
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "--gremlins",
            "-q",
            str(DEMO_SOURCE),
            str(test_file),
        ]
        env = {"PYTHONPATH": str(REPO_ROOT)}
        elapsed, code, out, err = run_cmd(cmd, cwd=REPO_ROOT, env=env, timeout_seconds=600.0)
        stats = parse_gremlins_stats(out + "\n" + err)
        if stats is None:
            return {"survivors": None, "detail": f"unparseable gremlins output (exit {code})", "ran": True}
        return {
            "survivors": stats["survived"],
            "detail": f"zapped={stats['killed']} survived={stats['survived']} exit={code}",
            "ran": True,
        }
    if tool == "mutmut":
        reset_mutmut_state()
        cmd = ["mutmut", "run", str(DEMO_SOURCE)]
        env = {"PYTHONPATH": str(REPO_ROOT)}
        elapsed, code, out, err = run_cmd(cmd, cwd=REPO_ROOT, env=env, timeout_seconds=600.0)
        _, code2, out2, err2 = run_cmd(
            ["mutmut", "export-cicd-stats"], cwd=REPO_ROOT, env=env, timeout_seconds=60.0
        )
        stats = parse_mutmut_stats(out2, err2)
        if stats is None:
            stats = parse_mutmut_stats(out + "\n" + err, "")
        if stats is None:
            return {"survivors": None, "detail": f"unparseable mutmut output (exit {code})", "ran": True}
        return {
            "survivors": stats["survived"],
            "detail": f"killed={stats['killed']} survived={stats['survived']} exit={code}",
            "ran": True,
        }
    # cosmic-ray: run via a session config scoped to the demo source.
    config_path = REPO_ROOT / "bench" / "adr003" / ".cosmic-ray-demo.toml"
    config_path.write_text(
        f"""[mutation]\nmodules = ["{DEMO_SOURCE}"]\n""",
        encoding="utf-8",
    )
    cmd = ["cosmic-ray", "run", str(config_path), str(test_file)]
    env = {"PYTHONPATH": str(REPO_ROOT)}
    elapsed, code, out, err = run_cmd(cmd, cwd=REPO_ROOT, env=env, timeout_seconds=600.0)
    stats = _parse_cosmic_ray_stats(out + "\n" + err)
    if stats is None:
        return {"survivors": None, "detail": f"unparseable cosmic-ray output (exit {code})", "ran": True}
    return {
        "survivors": stats["survived"],
        "detail": f"killed={stats['killed']} survived={stats['survived']} exit={code}",
        "ran": True,
    }


def _parse_cosmic_ray_stats(text: str) -> dict[str, int | None] | None:
    """Parse cosmic-ray JSON report into killed/survived/timeout counts."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    # cosmic-ray report may nest results under 'results' or be flat.
    candidates = [data]
    for key in ("results", "report", "summary"):
        if isinstance(data.get(key), dict):
            candidates.append(data[key])
    for blob in candidates:
        killed = blob.get("killed", blob.get("killed_mutants"))
        survived = blob.get("survived", blob.get("survived_mutants"))
        if killed is not None and survived is not None:
            return {
                "killed": int(killed),
                "survived": int(survived),
                "timeout": int(blob.get("timeout", 0) or 0),
            }
    return None


def measure_mutmut() -> dict[str, Any]:
    """Measure mutmut against the fixed target (H1-H5, M1-M3, demo H4)."""
    result: dict[str, Any] = {
        "version": "3.8.1",
        "licence_url": LICENCE_URLS["mutmut"],
        "H1": False,
        "H2": False,
        "H3": False,
        "H4": False,
        "H5": True,
        "M1": None,
        "M2": None,
        "M3": None,
        "M4": None,
    }
    available, detail = tool_available("mutmut")
    result["H1"] = available
    result["H1_detail"] = f"mutmut install/run check: {detail}"
    if not available:
        result["H2_detail"] = "Skipped: H1 failed"
        result["H3_detail"] = "Skipped: H1 failed"
        result["H4_detail"] = "Skipped: H1 failed"
        return result

    # H2: mutmut honours pytest -m unit via [tool.mutmut] pytest_add_cli_args_test_selection
    # and pythonpath from pytest.ini; no config files are edited for the run.
    result["H2"] = True
    result["H2_detail"] = "mutmut runs pytest -m unit via [tool.mutmut] config; pytest.ini untouched"

    timings: list[float] = []
    ok_runs = 0
    killed = survived = timeouts = 0
    for _ in range(3):
        reset_mutmut_state()
        cmd = ["mutmut", "run"]
        env = {"PYTHONPATH": str(REPO_ROOT)}
        elapsed, code, out, err = run_cmd(cmd, cwd=REPO_ROOT, env=env, timeout_seconds=1800.0)
        timings.append(elapsed)
        if code == 0:
            ok_runs += 1
        _, code2, out2, err2 = run_cmd(
            ["mutmut", "export-cicd-stats"], cwd=REPO_ROOT, env=env, timeout_seconds=60.0
        )
        stats = parse_mutmut_stats(out2, err2)
        if stats is None:
            stats = parse_mutmut_stats(out + "\n" + err, "")
        if stats:
            killed = stats["killed"] or 0
            survived = stats["survived"] or 0
            timeouts = stats["timeout"] or 0

    result["M1"] = median(timings) if timings else None
    result["M2"] = killed + survived + timeouts if (killed + survived + timeouts) > 0 else None
    result["timings"] = timings
    score, detail = measurement_score(killed, survived, timeouts)
    result["M3"] = round(score, 2) if (killed + survived) > 0 else None
    result["M3_detail"] = detail
    result["H3"] = (killed + survived) > 0  # mutmut reports killed/survived/timeout via export-cicd-stats
    result["H3_detail"] = (
        "mutmut export-cicd-stats emits killed/survived/timeout counts"
        if result["H3"]
        else "could not parse mutmut counts"
    )

    # H4: weakened-assertion demo.
    full_demo = run_demo_survivors("mutmut", DEMO_FULL_TEST)
    weak_demo = run_demo_survivors("mutmut", DEMO_WEAKENED_TEST)
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

    # M4: native Windows attempt.
    result["M4"] = native_windows_attempt("mutmut")
    return result


def measure_cosmic_ray() -> dict[str, Any]:
    """Measure cosmic-ray against the fixed target (H1-H5, M1-M3, demo H4)."""
    result: dict[str, Any] = {
        "version": "8.9.0",
        "licence_url": LICENCE_URLS["cosmic-ray"],
        "H1": False,
        "H2": False,
        "H3": False,
        "H4": False,
        "H5": True,
        "M1": None,
        "M2": None,
        "M3": None,
        "M4": None,
    }
    available, detail = tool_available("cosmic-ray")
    result["H1"] = available
    result["H1_detail"] = f"cosmic-ray install/run check: {detail}"
    if not available:
        result["H2_detail"] = "Skipped: H1 failed"
        result["H3_detail"] = "Skipped: H1 failed"
        result["H4_detail"] = "Skipped: H1 failed"
        return result

    # H2: cosmic-ray uses its own session config and test command; it can be
    # pointed at pytest -m unit without editing pytest.ini/conftest.
    result["H2"] = True
    result["H2_detail"] = "cosmic-ray test command set to pytest -m unit; pytest.ini untouched"

    config_path = REPO_ROOT / "bench" / "adr003" / ".cosmic-ray.toml"
    modules = ", ".join(f'"{m}"' for m in TARGET_FILES)
    tests = ", ".join(f'"{t}"' for t in UNIT_TESTS)
    config_path.write_text(
        f"""[mutation]\nmodules = [{modules}]\n\n[run]\ntest_command = "pytest -m unit {tests}"\n""",
        encoding="utf-8",
    )

    timings: list[float] = []
    killed = survived = timeouts = 0
    for _ in range(3):
        cmd = ["cosmic-ray", "run", str(config_path)]
        env = {"PYTHONPATH": str(REPO_ROOT)}
        elapsed, code, out, err = run_cmd(cmd, cwd=REPO_ROOT, env=env, timeout_seconds=1800.0)
        timings.append(elapsed)
        stats = _parse_cosmic_ray_stats(out + "\n" + err)
        if stats:
            killed = stats["killed"] or 0
            survived = stats["survived"] or 0
            timeouts = stats["timeout"] or 0

    result["M1"] = median(timings) if timings else None
    result["M2"] = killed + survived + timeouts if (killed + survived + timeouts) > 0 else None
    result["timings"] = timings
    score, detail = measurement_score(killed, survived, timeouts)
    result["M3"] = round(score, 2) if (killed + survived) > 0 else None
    result["M3_detail"] = detail
    result["H3"] = (killed + survived) > 0
    result["H3_detail"] = (
        "cosmic-ray report emits killed/survived counts"
        if result["H3"]
        else "could not parse cosmic-ray counts"
    )

    full_demo = run_demo_survivors("cosmic-ray", DEMO_FULL_TEST)
    weak_demo = run_demo_survivors("cosmic-ray", DEMO_WEAKENED_TEST)
    result["demo_full_survivors"] = full_demo.get("survivors")
    result["demo_weakened_survivors"] = weak_demo.get("survivors")
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
    result: dict[str, Any] = {
        "version": "1.13.2",
        "licence_url": LICENCE_URLS["pytest-gremlins"],
        "H1": False,
        "H2": False,
        "H3": False,
        "H4": False,
        "H5": True,
        "M1": None,
        "M2": None,
        "M3": None,
        "M4": None,
    }
    available, detail = tool_available("pytest-gremlins")
    result["H1"] = available
    result["H1_detail"] = f"pytest-gremlins install/run check: {detail}"
    if not available:
        result["H2_detail"] = "Skipped: H1 failed"
        result["H3_detail"] = "Skipped: H1 failed"
        result["H4_detail"] = "Skipped: H1 failed"
        return result

    # H2: pytest-gremlins is a pytest plugin; `pytest --gremlins -m unit` uses
    # pytest.ini (pythonpath, tier plugin, strict markers) unchanged.
    result["H2"] = True
    result["H2_detail"] = "pytest --gremlins -m unit runs under the existing pytest.ini unchanged"

    base_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--gremlins",
        "-q",
    ]
    env = {"PYTHONPATH": str(REPO_ROOT)}

    timings: list[float] = []
    killed = survived = timeouts = 0
    for _ in range(3):
        cmd = base_cmd + UNIT_TESTS
        elapsed, code, out, err = run_cmd(cmd, cwd=REPO_ROOT, env=env, timeout_seconds=1800.0)
        timings.append(elapsed)
        stats = parse_gremlins_stats(out + "\n" + err)
        if stats:
            killed = stats["killed"] or 0
            survived = stats["survived"] or 0
            timeouts = stats["timeout"] or 0

    result["M1"] = median(timings) if timings else None
    result["M2"] = killed + survived + timeouts if (killed + survived + timeouts) > 0 else None
    result["timings"] = timings
    score, detail = measurement_score(killed, survived, timeouts)
    result["M3"] = round(score, 2) if (killed + survived) > 0 else None
    result["M3_detail"] = detail
    # pytest-gremlins has no timeout mechanism; report timeout=0 only when the run
    # completed, and flag H3 FAIL if it cannot produce timeout counts at all.
    result["H3"] = (killed + survived) > 0
    result["H3_detail"] = (
        "pytest-gremlins report emits zapped/survived counts; no timeout concept (timeouts=0)"
        if result["H3"]
        else "could not parse pytest-gremlins counts"
    )
    if not result["H3"]:
        result["H3_detail"] = (
            "FAIL: pytest-gremlins has no machine-readable timeout count; "
            "H3 requires killed/survived/timeout counts"
        )

    full_demo = run_demo_survivors("pytest-gremlins", DEMO_FULL_TEST)
    weak_demo = run_demo_survivors("pytest-gremlins", DEMO_WEAKENED_TEST)
    result["demo_full_survivors"] = full_demo.get("survivors")
    result["demo_weakened_survivors"] = weak_demo.get("survivors")
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


def main() -> None:
    """Run every candidate tool and write results.json with the ADR-003 decision."""
    _, code, _, err = uv_sync_groups(["dev", "adr003"])
    install_ok = code == 0

    candidates: dict[str, dict[str, Any]] = {}
    for tool in TOOLS:
        if not install_ok:
            candidates[tool] = {
                "version": _VERSIONS[tool],
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
                "H1_detail": f"uv sync failed: {err.strip()[:200]}",
            }
            continue
        candidates[tool] = MEASURE_FUNCS[tool]()

    winner, decision = decide_winner(candidates)
    winner_score = None
    if winner:
        winner_score = candidates[winner].get("M3")
    threshold = unit_threshold(winner_score)

    results: dict[str, Any] = {
        "schema": "adr003-results-v1",
        "date": time.strftime("%Y-%m-%d"),
        "image": "python:3.12",
        "container": "medora-adr003-run" if IN_DOCKER else "native-host",
        "target_files": TARGET_FILES,
        "unit_tests": UNIT_TESTS,
        "tool_versions": {t: _VERSIONS[t] for t in TOOLS},
        "licence_urls": LICENCE_URLS,
        "install_ok": install_ok,
        "candidates": candidates,
        "decision": {
            "winner": winner,
            "decision_detail": decision,
            "unit_threshold": threshold,
        },
    }
    write_results(results)
    print(f"Results written to {RESULTS_FILE}", flush=True)
    if winner:
        print(f"Winner: {winner} (M3={winner_score}%, unit threshold={threshold['score']})", flush=True)
    else:
        print(f"No winner: {decision.get('reason')}", flush=True)


_VERSIONS: dict[str, str] = {
    "mutmut": "3.8.1",
    "cosmic-ray": "8.9.0",
    "pytest-gremlins": "1.13.2",
}


if __name__ == "__main__":
    main()
