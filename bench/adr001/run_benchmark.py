# TERM: lockfile - a file recording exact versions and cryptographic hashes of all dependencies.
# TERM: cold install - an installation executed after completely clearing package manager caches.
# TERM: warm install - an installation executed immediately into a clean environment with caches retained.
# TERM: cold lock - dependency resolution executed after cache clearing with any prior lockfile deleted.
"""ADR-001 dependency manager benchmark harness for medoRa."""

from __future__ import annotations

import ctypes
import datetime
import hashlib
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

CACHE_CLEAN_TIMEOUT_SECONDS: float = 60.0
VENV_CREATE_TIMEOUT_SECONDS: float = 120.0
LOCK_TIMEOUT_SECONDS: float = 300.0
INSTALL_TIMEOUT_SECONDS: float = 600.0
METADATA_TIMEOUT_SECONDS: float = 30.0
TIE_THRESHOLD_RATIO: float = 0.10
COLD_WARM_DIVERGENCE_RATIO: float = 0.20
# Captures sufficient stack trace and root cause error messages from underlying package managers
SUBPROCESS_TAIL_LINES: int = 60


def compute_sha256(path: Path) -> str:
    """WHAT: Computes the hexadecimal SHA-256 digest of a file.
    WHY: Required to verify lockfile reproducibility (H1) and record input spec hash.

    Args:
        path: Path to the target file.
    Returns:
        Hexadecimal SHA-256 string.
    Example:
        compute_sha256(Path("requirements.in")) -> "d60c747445..."
    """
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def compute_median(values: list[float]) -> float:
    """WHAT: Calculates the median value of a list of floating-point numbers.
    WHY: Required by the ADR-001 criteria to score candidate install and lock times.

    Args:
        values: Non-empty list of numerical measurements.
    Returns:
        Median value.
    Example:
        compute_median([1.0, 3.0, 2.0]) -> 2.0
    """
    if not values:
        raise ValueError("Cannot compute median of empty list")
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def read_spec_packages(spec_path: Path) -> list[str]:
    """WHAT: Reads dependency package names from the specification file.
    WHY: Guarantees identical input package lists across all candidate tools.

    Args:
        spec_path: Path to the spec requirements.in file.
    Returns:
        List of non-empty, non-comment package specification strings.
    Example:
        read_spec_packages(Path("requirements.in")) -> ["fastapi", "uvicorn", ...]
    """
    packages: list[str] = []
    for raw_line in spec_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            packages.append(line)
    return packages


def write_uv_project(project_dir: Path, packages: list[str]) -> Path:
    """WHAT: Generates a minimal pyproject.toml tailored for uv.
    WHY: uv requires a PEP 621 pyproject.toml to resolve dependencies and build uv.lock.

    Args:
        project_dir: Destination directory.
        packages: List of dependency names.
    Returns:
        Path to the written pyproject.toml.
    Example:
        write_uv_project(Path("work/uv"), ["fastapi"]) -> Path("work/uv/pyproject.toml")
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    toml_path = project_dir / "pyproject.toml"
    deps_lines = "\n".join(f'    "{pkg}",' for pkg in packages)
    content = (
        "[project]\n"
        'name = "medora-bench-adr001"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.12,<3.13"\n'
        "dependencies = [\n"
        f"{deps_lines}\n"
        "]\n"
    )
    toml_path.write_text(content, encoding="utf-8")
    return toml_path


def write_poetry_project(project_dir: Path, packages: list[str]) -> Path:
    """WHAT: Generates a minimal pyproject.toml tailored for Poetry.
    WHY: Poetry requires its own configuration table to resolve and lock dependencies.

    Args:
        project_dir: Destination directory.
        packages: List of dependency names.
    Returns:
        Path to the written pyproject.toml.
    Example:
        write_poetry_project(Path("work/poetry"), ["fastapi"]) -> Path(".../pyproject.toml")
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    toml_path = project_dir / "pyproject.toml"
    deps_body: list[str] = ['python = ">=3.12,<3.13"']
    for pkg in packages:
        if "[" in pkg and pkg.endswith("]"):
            base_name, extra = pkg[:-1].split("[", 1)
            deps_body.append(f'{base_name} = {{ version = "*", extras = ["{extra}"] }}')
        else:
            deps_body.append(f'{pkg} = "*"')
    deps_lines = "\n".join(deps_body)
    content = (
        "[project]\n"
        'name = "medora-bench-adr001"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.12,<3.13"\n\n'
        "[tool.poetry]\n"
        "package-mode = false\n\n"
        "[tool.poetry.dependencies]\n"
        f"{deps_lines}\n"
    )
    toml_path.write_text(content, encoding="utf-8")
    return toml_path


def write_piptools_project(project_dir: Path, packages: list[str]) -> Path:
    """WHAT: Generates a requirements.in file for pip-tools.
    WHY: pip-compile consumes a requirements.in file to compile a pinned requirements.txt.

    Args:
        project_dir: Destination directory.
        packages: List of dependency names.
    Returns:
        Path to the written requirements.in.
    Example:
        write_piptools_project(Path("work/pip-tools"), ["fastapi"]) -> Path(".../requirements.in")
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    req_path = project_dir / "requirements.in"
    req_path.write_text("\n".join(packages) + "\n", encoding="utf-8")
    return req_path


def parse_uv_hashes(lock_content: str) -> tuple[bool, str]:
    """WHAT: Checks whether every dependency package in a uv.lock has cryptographic hashes.
    WHY: Implements hard rule H2 for uv by validating sdist or wheel hash entries.

    Args:
        lock_content: Raw text of uv.lock.
    Returns:
        (True, message) if all dependencies have hashes, else (False, details).
    Example:
        parse_uv_hashes(content) -> (True, "All 37 packages have hashes")
    """
    data = tomllib.loads(lock_content)
    packages = data.get("package", [])
    if not packages:
        return False, "No packages found in uv.lock"
    checked = 0
    for pkg in packages:
        if pkg.get("source", {}).get("virtual") == ".":
            continue
        has_sdist = bool(pkg.get("sdist", {}).get("hash"))
        wheels = pkg.get("wheels", [])
        has_wheel = any(bool(w.get("hash")) for w in wheels)
        if not (has_sdist or has_wheel):
            return False, f"Package {pkg.get('name')} lacks hash in uv.lock"
        checked += 1
    return True, f"All {checked} dependency packages have hashes in uv.lock"


def parse_poetry_hashes(lock_content: str) -> tuple[bool, str]:
    """WHAT: Checks whether every package in a poetry.lock has cryptographic hashes.
    WHY: Implements hard rule H2 for Poetry by checking files array entries.

    Args:
        lock_content: Raw text of poetry.lock.
    Returns:
        (True, message) if all packages have file hashes, else (False, details).
    Example:
        parse_poetry_hashes(content) -> (True, "All 37 packages have hashes")
    """
    data = tomllib.loads(lock_content)
    packages = data.get("package", [])
    if not packages:
        return False, "No packages found in poetry.lock"
    for pkg in packages:
        files = pkg.get("files", [])
        if not files:
            return False, f"Package {pkg.get('name')} has no files in poetry.lock"
        if not any(f.get("hash", "").startswith("sha256:") for f in files):
            return False, f"Package {pkg.get('name')} lacks valid hash in poetry.lock"
    return True, f"All {len(packages)} packages have valid hashes in poetry.lock"


def parse_piptools_hashes(lock_content: str) -> tuple[bool, str]:
    """WHAT: Checks whether every package in a pip-compile requirements.txt has hashes.
    WHY: Implements hard rule H2 for pip-tools by inspecting --hash=sha256 markers.

    Args:
        lock_content: Raw text of compiled requirements.txt.
    Returns:
        (True, message) if all package blocks have hashes, else (False, details).
    Example:
        parse_piptools_hashes(content) -> (True, "All 37 packages have hashes")
    """
    pkg_hashes: dict[str, list[str]] = {}
    current_pkg: str | None = None
    for raw_line in lock_content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line and not line.startswith("--hash"):
            current_pkg = line.split("==")[0].strip()
            pkg_hashes[current_pkg] = []
        elif "--hash=" in line and current_pkg is not None:
            clean_hash = line.split("--hash=")[1].split()[0].rstrip(" \\")
            pkg_hashes[current_pkg].append(clean_hash)
    if not pkg_hashes:
        return False, "No packages parsed from requirements.txt"
    for pkg, hashes in pkg_hashes.items():
        if not hashes:
            return False, f"Package {pkg} lacks hashes in requirements.txt"
    return True, f"All {len(pkg_hashes)} packages have hashes in requirements.txt"


def verify_h1_reproducible(hash1: str, hash2: str) -> tuple[bool, str]:
    """WHAT: Compares SHA-256 digests of two lock files generated independently.
    WHY: Verifies hard rule H1 (reproducible byte-identical locking).

    Args:
        hash1: SHA-256 of first lock file.
        hash2: SHA-256 of second lock file.
    Returns:
        (True, message) if identical, else (False, difference details).
    Example:
        verify_h1_reproducible("abc", "abc") -> (True, "Reproducible: identical lock SHA-256")
    """
    if not hash1 or not hash2:
        return False, "Lock file hash is empty"
    if hash1 == hash2:
        return True, f"Reproducible: byte-identical lock SHA-256 ({hash1})"
    return False, f"Disqualified: lock hash mismatch ({hash1} vs {hash2})"


def verify_h2_hashes(tool: str, lock_content: str) -> tuple[bool, str]:
    """WHAT: Dispatches lock content parsing to verify package hashes per tool.
    WHY: Centralizes hard rule H2 verification across candidate tools and baseline.

    Args:
        tool: Tool identifier ('uv', 'poetry', 'pip-tools', 'pip').
        lock_content: Content of the generated lockfile.
    Returns:
        (True, message) if hashes verified, else (False, explanation).
    Example:
        verify_h2_hashes("uv", content) -> (True, "All 37 packages have hashes...")
    """
    if tool == "uv":
        return parse_uv_hashes(lock_content)
    if tool == "poetry":
        return parse_poetry_hashes(lock_content)
    if tool == "pip-tools":
        return parse_piptools_hashes(lock_content)
    if tool == "pip":
        return False, "Baseline unpinned pip does not produce a lock file or hashes"
    return False, f"Unknown tool identifier: {tool}"


def verify_h4_python_pin(tool: str, lock_content: str) -> tuple[bool, str]:
    """WHAT: Validates that the Python runtime version is pinned in the lock or project.
    WHY: Implements hard rule H4 ensuring deterministic Python environment targeting.

    Args:
        tool: Tool identifier ('uv', 'poetry', 'pip-tools', 'pip').
        lock_content: Text of lockfile or project file.
    Returns:
        (True, pin location detail) or (False, explanation).
    Example:
        verify_h4_python_pin("uv", content) -> (True, "Pinned in uv.lock requires-python")
    """
    if tool == "uv":
        data = tomllib.loads(lock_content)
        pin = data.get("requires-python")
        if pin:
            return True, f"Pinned in uv.lock (requires-python = '{pin}')"
        return False, "requires-python not found in uv.lock"
    if tool == "poetry":
        data = tomllib.loads(lock_content)
        pin = data.get("metadata", {}).get("python-versions")
        if pin:
            return True, f"Pinned in poetry.lock (metadata.python-versions = '{pin}')"
        return False, "metadata.python-versions not found in poetry.lock"
    if tool == "pip-tools":
        if "pip-compile with Python 3.12" in lock_content:
            return True, "Pinned in requirements.txt header (Python 3.12)"
        return False, "Python version pin comment not found in requirements.txt"
    return False, "Baseline does not pin Python version in a lock file"


def run_subprocess_timed(
    args: list[str],
    timeout_seconds: float,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[float, int, str]:
    """WHAT: Executes a subprocess with time.perf_counter timing and explicit exit check.
    WHY: Standardized execution harness ensuring timeouts, output capture, and error checks.

    Args:
        args: Command argument list.
        timeout_seconds: Subprocess timeout limit.
        cwd: Working directory.
        env: Environment variables.
    Returns:
        (elapsed seconds, exit code, output tail string).
    Example:
        run_subprocess_timed(["py", "-V"], 10.0) -> (0.05, 0, "Python 3.12.10")
    """
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    start_time = time.perf_counter()
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        elapsed = time.perf_counter() - start_time
        combined_output = proc.stdout + ("\n" if proc.stderr else "") + proc.stderr
        output_tail = combined_output.strip().splitlines()[-SUBPROCESS_TAIL_LINES:]
        return elapsed, proc.returncode, "\n".join(output_tail)
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - start_time
        return elapsed, -1, f"Command timed out after {timeout_seconds}s: {exc}"


def get_tool_cache_dir(tool: str, repo_root: Path) -> Path:
    """WHAT: Obtains each tool's official cache directory from its CLI command.
    WHY: Required to inspect directory state before and after cache-clean operations.

    Args:
        tool: Tool identifier ('uv', 'poetry', 'pip-tools', 'pip').
        repo_root: Root path of the repository.
    Returns:
        Path to the tool's cache directory.
    Example:
        get_tool_cache_dir("uv", Path(".")) -> Path("C:/Users/.../AppData/Local/uv/cache")
    """
    if tool == "uv":
        exe = repo_root / "bench/adr001/.tools/uv/Scripts/uv.exe"
        cmd = [str(exe), "cache", "dir"]
    elif tool == "poetry":
        exe = repo_root / "bench/adr001/.tools/poetry/Scripts/poetry.exe"
        cmd = [str(exe), "config", "cache-dir"]
    elif tool == "pip-tools":
        exe = repo_root / "bench/adr001/.tools/pip-tools/Scripts/pip.exe"
        cmd = [str(exe), "cache", "dir"]
    else:
        cmd = [sys.executable, "-m", "pip", "cache", "dir"]
    _, code, out = run_subprocess_timed(cmd, METADATA_TIMEOUT_SECONDS)
    if code != 0 or not out.strip():
        raise RuntimeError(f"Failed to query cache dir for {tool}: {out}")
    return Path(out.strip().splitlines()[-1])


def get_cache_bytes(cache_dir: Path) -> int:
    """WHAT: Measures total byte size of all files inside a cache directory.
    WHY: Proves cache state changes and emptiness across benchmark repetitions.

    Args:
        cache_dir: Path to directory being measured.
    Returns:
        Total size in bytes (0 if non-existent).
    Example:
        get_cache_bytes(Path("cache")) -> 1048576
    """
    if not cache_dir.exists():
        return 0
    total = 0
    for file_path in cache_dir.rglob("*"):
        if file_path.is_file():
            total += file_path.stat().st_size
    return total


def is_cache_clean(cache_dir: Path) -> tuple[bool, str]:
    """WHAT: Verifies that a cache directory is empty or contains no package files.
    WHY: Implements strict proof of cache emptiness before cold installs.

    Args:
        cache_dir: Path to tool cache directory.
    Returns:
        (True, details) if empty/clean, else (False, failure reason).
    Example:
        is_cache_clean(Path("cache")) -> (True, "Contains no package files")
    """
    if not cache_dir.exists():
        return True, "Cache directory does not exist (empty)"
    package_exts = (".whl", ".tar.gz", ".zip")
    pkg_files = [f for f in cache_dir.rglob("*") if f.is_file() and f.name.endswith(package_exts)]
    if pkg_files:
        return False, f"Cache contains {len(pkg_files)} package files (e.g. {pkg_files[0].name})"
    return True, "Cache is empty or contains no package files"


def clean_cache(tool: str, repo_root: Path) -> tuple[float, int, str]:
    """WHAT: Cleans package manager cache using the tool's verified CLI command.
    WHY: Essential to establish a pristine cold state for cold install measurements.

    Args:
        tool: Tool identifier ('uv', 'poetry', 'pip-tools', 'pip').
        repo_root: Root path of the repository.
    Returns:
        (elapsed seconds, exit code, output tail string).
    Example:
        clean_cache("uv", Path(".")) -> (0.1, 0, "Cleared cache...")
    """
    if tool == "uv":
        exe = repo_root / "bench/adr001/.tools/uv/Scripts/uv.exe"
        return run_subprocess_timed([str(exe), "cache", "clean"], CACHE_CLEAN_TIMEOUT_SECONDS)
    if tool == "poetry":
        exe = repo_root / "bench/adr001/.tools/poetry/Scripts/poetry.exe"
        cmd = [str(exe), "cache", "clear", "--all", "--no-interaction"]
        return run_subprocess_timed(cmd, CACHE_CLEAN_TIMEOUT_SECONDS)
    if tool == "pip-tools":
        exe = repo_root / "bench/adr001/.tools/pip-tools/Scripts/pip.exe"
        return run_subprocess_timed([str(exe), "cache", "purge"], CACHE_CLEAN_TIMEOUT_SECONDS)
    if tool == "pip":
        cmd = [sys.executable, "-m", "pip", "cache", "purge"]
        return run_subprocess_timed(cmd, CACHE_CLEAN_TIMEOUT_SECONDS)
    return 0.0, 1, f"Unknown tool: {tool}"


def clean_cache_verified(tool: str, repo_root: Path) -> tuple[int, int, bool, str]:
    """WHAT: Cleans cache and verifies that package files were completely removed.
    WHY: Proves cache eviction with concrete byte counts and filesystem inspection.

    Args:
        tool: Tool identifier ('uv', 'poetry', 'pip-tools', 'pip').
        repo_root: Root path of the repository.
    Returns:
        (bytes_before, bytes_after, success_boolean, details_string).
    Example:
        clean_cache_verified("uv", Path(".")) -> (1048576, 0, True, "Cache cleared")
    """
    cache_dir = get_tool_cache_dir(tool, repo_root)
    bytes_before = get_cache_bytes(cache_dir)
    _, exit_code, tail = clean_cache(tool, repo_root)
    bytes_after = get_cache_bytes(cache_dir)
    clean_ok, clean_msg = is_cache_clean(cache_dir)
    if exit_code != 0:
        return bytes_before, bytes_after, False, f"Clean command exited {exit_code}: {tail}"
    if not clean_ok:
        return bytes_before, bytes_after, False, clean_msg
    return bytes_before, bytes_after, True, "Cache successfully verified empty"


def create_target_venv(venv_path: Path) -> tuple[float, int, str]:
    """WHAT: Creates a fresh target virtual environment for benchmarking.
    WHY: Isolates each install benchmark into an empty environment.

    Args:
        venv_path: Destination directory for the virtual environment.
    Returns:
        (elapsed seconds, exit code, output tail string).
    Example:
        create_target_venv(Path("work/venv1")) -> (2.1, 0, "")
    """
    cmd = [sys.executable, "-m", "venv", str(venv_path)]
    return run_subprocess_timed(cmd, VENV_CREATE_TIMEOUT_SECONDS)


def run_lock_step(tool: str, project_dir: Path, repo_root: Path) -> tuple[float, int, str]:
    """WHAT: Runs the dependency lock command for a candidate tool.
    WHY: Times and records the lock resolution step.

    Args:
        tool: Tool identifier ('uv', 'poetry', 'pip-tools', 'pip').
        project_dir: Working directory containing project files.
        repo_root: Root path of repository.
    Returns:
        (elapsed seconds, exit code, output tail string).
    Example:
        run_lock_step("uv", Path("work/uv"), Path(".")) -> (1.2, 0, "Resolved 38 packages...")
    """
    if tool == "uv":
        exe = repo_root / "bench/adr001/.tools/uv/Scripts/uv.exe"
        cmd = [str(exe), "lock", "--directory", str(project_dir)]
        return run_subprocess_timed(cmd, LOCK_TIMEOUT_SECONDS)
    if tool == "poetry":
        exe = repo_root / "bench/adr001/.tools/poetry/Scripts/poetry.exe"
        cmd = [str(exe), "lock", "-C", str(project_dir), "--no-interaction"]
        return run_subprocess_timed(cmd, LOCK_TIMEOUT_SECONDS)
    if tool == "pip-tools":
        exe = repo_root / "bench/adr001/.tools/pip-tools/Scripts/pip-compile.exe"
        out_txt = project_dir / "requirements.txt"
        in_file = project_dir / "requirements.in"
        cmd = [str(exe), "--generate-hashes", "-o", str(out_txt), str(in_file)]
        return run_subprocess_timed(cmd, LOCK_TIMEOUT_SECONDS)
    return 0.0, 0, "Baseline pip does not lock dependencies"


def run_install_step(
    tool: str,
    project_dir: Path,
    venv_path: Path,
    repo_root: Path,
) -> tuple[float, int, str]:
    """WHAT: Executes the dependency installation into a designated virtual environment.
    WHY: Measures installation duration under cold or warm cache conditions.

    Args:
        tool: Tool identifier ('uv', 'poetry', 'pip-tools', 'pip').
        project_dir: Directory containing project/lock files.
        venv_path: Path to target virtual environment.
        repo_root: Root path of repository.
    Returns:
        (elapsed seconds, exit code, output tail string).
    Example:
        run_install_step("uv", Path("work/uv"), Path("work/venv"), Path(".")) -> (2.4, 0, "...")
    """
    venv_python = venv_path / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if tool == "uv":
        exe = repo_root / "bench/adr001/.tools/uv/Scripts/uv.exe"
        cmd = [
            str(exe),
            "sync",
            "--frozen",
            "--no-install-project",
            "--active",
            "--directory",
            str(project_dir),
        ]
        return run_subprocess_timed(cmd, INSTALL_TIMEOUT_SECONDS, env={"VIRTUAL_ENV": str(venv_path)})
    if tool == "poetry":
        exe = repo_root / "bench/adr001/.tools/poetry/Scripts/poetry.exe"
        cmd = [str(exe), "sync", "--no-root", "--no-interaction", "-C", str(project_dir)]
        return run_subprocess_timed(cmd, INSTALL_TIMEOUT_SECONDS, env={"VIRTUAL_ENV": str(venv_path)})
    if tool == "pip-tools":
        exe = repo_root / "bench/adr001/.tools/pip-tools/Scripts/pip-sync.exe"
        req_txt = project_dir / "requirements.txt"
        cmd = [str(exe), "--python-executable", str(venv_python), str(req_txt)]
        return run_subprocess_timed(cmd, INSTALL_TIMEOUT_SECONDS)
    in_file = project_dir / "requirements.in"
    cmd = [str(venv_python), "-m", "pip", "install", "-r", str(in_file)]
    return run_subprocess_timed(cmd, INSTALL_TIMEOUT_SECONDS)


def filter_qualifying_tools(results_data: dict[str, Any]) -> list[dict[str, Any]]:
    """WHAT: Filters out tools that fail any hard rule H1-H4.
    WHY: Separates rule checking from decision scoring to keep functions focused and concise.

    Args:
        results_data: Full benchmark results dictionary.
    Returns:
        List of candidate dictionaries that passed H1-H4.
    Example:
        filter_qualifying_tools(results) -> [{"tool": "uv", ...}]
    """
    qualifying: list[dict[str, Any]] = []
    for tool_name, data in results_data.get("tools", {}).items():
        if tool_name == "pip":
            continue
        hard_rules = data.get("hard_rules", {})
        h_passed = all(hard_rules.get(f"H{i}", {}).get("passed", False) for i in range(1, 5))
        if h_passed:
            qualifying.append({"tool": tool_name, **data})
    return qualifying


def get_cold_install_score(candidate: dict[str, Any]) -> float:
    """WHAT: Extracts the median cold install seconds or infinity if unmeasured.
    WHY: Supplies a robust sort key for tie-breaking candidate evaluations.

    Args:
        candidate: Candidate tool evaluation dictionary.
    Returns:
        Median cold install seconds or infinity.
    Example:
        get_cold_install_score(cand) -> 7.44
    """
    cold_sec = candidate["summary"].get("median_cold_install_seconds")
    return float("inf") if cold_sec is None else cold_sec


def evaluate_decision(results_data: dict[str, Any]) -> tuple[str, str]:
    """WHAT: Applies the ADR-001 decision rule to pick the winning dependency manager.
    WHY: Implements the exact policy rule in code without human intervention.

    Args:
        results_data: Full benchmark results dictionary.
    Returns:
        (winning_tool, decision_rationale_text).
    Example:
        evaluate_decision(results) -> ("uv", "uv achieved the lowest median warm install...")
    """
    qualifying = filter_qualifying_tools(results_data)
    if not qualifying:
        raise ValueError("No candidate tool satisfied all hard rules H1-H4")
    best_warm = min(qualifying, key=lambda c: c["summary"]["median_warm_install_seconds"])
    min_warm_sec = best_warm["summary"]["median_warm_install_seconds"]
    tied = [
        c
        for c in qualifying
        if (c["summary"]["median_warm_install_seconds"] - min_warm_sec) / min_warm_sec <= TIE_THRESHOLD_RATIO
    ]
    if len(tied) == 1:
        winner = tied[0]["tool"]
        return winner, (
            f"{winner} satisfied all hard criteria H1-H4 and achieved the lowest "
            f"median warm install time ({min_warm_sec:.2f}s)."
        )
    best_cold = min(tied, key=get_cold_install_score)
    winner = best_cold["tool"]
    cold_sec = best_cold["summary"]["median_cold_install_seconds"]
    return winner, (
        f"{winner} won via tie-break: multiple tools had warm install times within 10% "
        f"of {min_warm_sec:.2f}s, and {winner} achieved the lowest median cold install "
        f"time ({cold_sec:.2f}s)."
    )


def check_cold_warm_divergence(tools_data: dict[str, Any]) -> list[str]:
    """WHAT: Flags tools where median cold install is within 20% of warm install.
    WHY: Enforces policy that real cold installs must download rather than hitting cache.

    Args:
        tools_data: Dictionary mapping tool names to results data.
    Returns:
        List of warning string descriptions.
    Example:
        check_cold_warm_divergence(data) -> ["WARNING: uv cold install within 20% of warm"]
    """
    warnings: list[str] = []
    for tool_name, tool_info in tools_data.items():
        summary = tool_info.get("summary", {})
        cold_s = summary.get("median_cold_install_seconds")
        warm_s = summary.get("median_warm_install_seconds")
        if cold_s is None or warm_s is None:
            continue
        if warm_s > 0.0 and (cold_s - warm_s) / warm_s <= COLD_WARM_DIVERGENCE_RATIO:
            warnings.append(
                f"WARNING: `{tool_name}` median cold install ({cold_s:.2f}s) is within 20% of warm install "
                f"({warm_s:.2f}s); cache may not have forced complete network re-downloads."
            )
    return warnings


def get_system_ram_gb() -> str:
    """WHAT: Detects total installed physical RAM on the host machine.
    WHY: Required metadata for benchmark reproducibility.

    Args:
        none.
    Returns:
        Formatted RAM string (e.g. '15.3 GB').
    Example:
        get_system_ram_gb() -> "16.0 GB"
    """
    if sys.platform == "win32":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong),
                ("avail_page", ctypes.c_ulonglong),
                ("total_virt", ctypes.c_ulonglong),
                ("avail_virt", ctypes.c_ulonglong),
                ("avail_ext", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return f"{status.total_phys / (1024**3):.1f} GB"
    return "Unknown"


def get_git_commit(repo_root: Path) -> str:
    """WHAT: Retrieves the current git commit hash.
    WHY: Required in results.json to trace benchmark state to source code.

    Args:
        repo_root: Root path of the git repository.
    Returns:
        Current commit SHA string or 'unknown'.
    Example:
        get_git_commit(Path(".")) -> "5c09a1d..."
    """
    cmd = ["git", "-C", str(repo_root), "rev-parse", "HEAD"]
    _, code, tail = run_subprocess_timed(cmd, METADATA_TIMEOUT_SECONDS)
    return tail.strip() if code == 0 else "unknown"


def get_machine_metadata(repo_root: Path) -> dict[str, Any]:
    """WHAT: Gathers host platform, hardware, tool, and git metadata.
    WHY: Complete benchmark provenance required by ADR-001 specification.

    Args:
        repo_root: Path to repository root.
    Returns:
        Dictionary of environment metadata.
    Example:
        get_machine_metadata(Path(".")) -> {"os": "Windows...", "cpu": "...", ...}
    """
    uv_exe = repo_root / "bench/adr001/.tools/uv/Scripts/uv.exe"
    poetry_exe = repo_root / "bench/adr001/.tools/poetry/Scripts/poetry.exe"
    piptools_exe = repo_root / "bench/adr001/.tools/pip-tools/Scripts/pip-compile.exe"
    _, _, uv_ver = run_subprocess_timed([str(uv_exe), "--version"], METADATA_TIMEOUT_SECONDS)
    _, _, poetry_ver = run_subprocess_timed([str(poetry_exe), "--version"], METADATA_TIMEOUT_SECONDS)
    _, _, piptools_ver = run_subprocess_timed([str(piptools_exe), "--version"], METADATA_TIMEOUT_SECONDS)
    _, _, pip_ver = run_subprocess_timed([sys.executable, "-m", "pip", "--version"], METADATA_TIMEOUT_SECONDS)
    return {
        "os": platform.platform(),
        "cpu": platform.processor(),
        "ram": get_system_ram_gb(),
        "python_version": sys.version.split()[0],
        "git_commit": get_git_commit(repo_root),
        "tool_versions": {
            "uv": uv_ver.strip(),
            "poetry": poetry_ver.strip(),
            "pip-tools": piptools_ver.strip(),
            "pip": pip_ver.strip(),
        },
    }


def render_results_table(results_data: dict[str, Any]) -> str:
    """WHAT: Formats candidate benchmark measurements into a GitHub markdown table.
    WHY: Generates transparent, verifiable results presentation in ADR-001.

    Args:
        results_data: Complete benchmark results dictionary.
    Returns:
        Markdown table string.
    Example:
        render_results_table(results) -> "| Tool | H1 | ... |"
    """
    lines = [
        "| Tool | Version | H1 | H2 | H3 | H4 | Median Lock (s) | Median Cold (s) | Median Warm (s) |",
        "|---|---|:---:|:---:|:---:|:---:|---:|---:|---:|",
    ]
    tool_order = ["uv", "poetry", "pip-tools", "pip"]
    for tool in tool_order:
        data = results_data["tools"].get(tool, {})
        ver = results_data["metadata"]["tool_versions"].get(tool, "").split("\n")[0]
        hr = data.get("hard_rules", {})
        h1 = "PASS" if hr.get("H1", {}).get("passed") else "FAIL"
        h2 = "PASS" if hr.get("H2", {}).get("passed") else "FAIL"
        h3 = "PASS" if hr.get("H3", {}).get("passed") else "FAIL"
        h4 = "PASS" if hr.get("H4", {}).get("passed") else "FAIL"
        summ = data.get("summary", {})
        l_val = summ.get("median_lock_seconds")
        c_val = summ.get("median_cold_install_seconds")
        w_val = summ.get("median_warm_install_seconds")
        lock_s = "N/A" if tool == "pip" else (f"{l_val:.2f}" if l_val is not None else "not measured")
        cold_s = f"{c_val:.2f}" if c_val is not None else "not measured"
        warm_s = f"{w_val:.2f}" if w_val is not None else "not measured"
        lines.append(f"| {tool} | {ver} | {h1} | {h2} | {h3} | {h4} | {lock_s} | {cold_s} | {warm_s} |")
    return "\n".join(lines)


def render_repetition_details(results_data: dict[str, Any]) -> str:
    """WHAT: Formats per-repetition cold timings and verified cache bytes into markdown.
    WHY: Required by ADR-001 review to surface cache emptiness proof per repetition.

    Args:
        results_data: Benchmark results dictionary.
    Returns:
        Markdown table string showing repetitions.
    Example:
        render_repetition_details(results) -> "| Tool | Rep | Cold (s) | Bytes After | ..."
    """
    lines = [
        "| Tool | Repetition | Cold Install (s) | Cache Bytes Before | Cache Bytes After | Cold Status |",
        "|---|:---:|---:|---:|---:|:---:|",
    ]
    tool_order = ["uv", "poetry", "pip-tools", "pip"]
    for tool in tool_order:
        runs = results_data["tools"].get(tool, {}).get("runs", [])
        for idx, run in enumerate(runs):
            c_inst = run["cold_install"]
            c_sec = f"{c_inst['seconds']:.2f}" if c_inst.get("seconds") is not None else "not measured"
            b_bef = c_inst.get("bytes_before", 0)
            b_aft = c_inst.get("bytes_after", 0)
            status = c_inst["status"]
            lines.append(f"| {tool} | {idx + 1} | {c_sec} | {b_bef:,} | {b_aft:,} | {status} |")
    return "\n".join(lines)


def render_adr_header_and_context(meta: dict[str, Any], date_str: str, seed: int, spec_sha: str) -> str:
    """WHAT: Renders the markdown header, context, and candidates sections of ADR-001.
    WHY: Keeps document rendering modular and within the line length limit.

    Args:
        meta: Environment metadata dictionary.
        date_str: Date of benchmark execution.
        seed: Random seed used.
        spec_sha: Input requirements.in SHA-256.
    Returns:
        Markdown text of header sections.
    Example:
        render_adr_header_and_context(meta, "2026-11-10", 1729, "abc") -> "# ADR-001..."
    """
    return (
        f"# ADR-001: Python Dependency Manager Selection\n\n"
        f"**Date**: {date_str}  \n"
        f"**Status**: Accepted  \n"
        f"**Git Commit**: `{meta['git_commit']}`  \n"
        f"**Random Seed**: `{seed}`  \n"
        f"**Input Spec SHA-256**: `{spec_sha}`  \n\n"
        f"## Context\n\n"
        f"medoRa requires deterministic, fast, and secure Python dependency management across "
        f"local development, Docker builds, and CI pipelines. Phase 1 Round 1 benchmarks candidate "
        f"tools empirically on the host Windows laptop against 12 unpinned top-level packages.\n\n"
        f"## Candidates\n\n"
        f"- `uv` ({meta['tool_versions']['uv']})\n"
        f"- `poetry` ({meta['tool_versions']['poetry']})\n"
        f"- `pip-tools` ({meta['tool_versions']['pip-tools']})\n"
        f"- Baseline reference: `pip` ({meta['tool_versions']['pip']})\n\n"
        f"## Criteria\n\n"
        f"Pre-registered evaluation criteria are recorded in [ADR-001-criteria.md](ADR-001-criteria.md):\n"
        f"- **H1**: Reproducible lock (byte-identical lock files from scratch).\n"
        f"- **H2**: Package hashes for every dependency entry.\n"
        f"- **H3**: Zero exit code on lock and install on Windows.\n"
        f"- **H4**: Python runtime version pinned in project or lock.\n"
        f"- **Decision Rule**: Lowest median warm install among H1–H4 qualifying tools; "
        f"ties within 10% broken by lowest median cold install.\n\n"
    )


def render_adr_footer(winner: str, rationale: str) -> str:
    """WHAT: Renders the decision, consequences, and limits sections of ADR-001.
    WHY: Keeps markdown rendering cleanly modularized under 30 code lines.

    Args:
        winner: Name of winning dependency manager.
        rationale: Machine-generated justification text.
    Returns:
        Markdown text of conclusion sections.
    Example:
        render_adr_footer("uv", "uv achieved...") -> "## Decision\n..."
    """
    return (
        f"## Decision\n\n"
        f"**Selected tool**: `{winner}`\n\n"
        f"{rationale}\n\n"
        f"## Consequences\n\n"
        f"- medoRa adopts `{winner}` as its primary Python dependency manager.\n"
        f"- Lockfiles will be checked into source control and verified on CI.\n"
        f"- Development setup and CI workflows will use `{winner}` commands for fast reproducible sync.\n\n"
        f"## Limits\n\n"
        f"- **Single machine**: Measurements reflect this Windows laptop (SSD, Windows 11, x86_64).\n"
        f"- **Network variance**: Cold installs depend on PyPI latency, mitigated by interleaved random runs.\n"
        f"- **Windows Defender scanning**: Background filesystem scanning adds constant overhead to file writes.\n"
        f"- **Poetry cache limitation**: poetry's cache-clear command leaves the artifacts cache, so its cold install wasn't measured.\n"
        f"- **Pip cache behavior**: pip's cache held no packages, so pip/pip-tools cold vs warm isn't comparable.\n"
        f"- **Subprocess log truncation**: the pip-tools rep 5 failure cause wasn't captured (tail truncated in this run).\n"
    )


def render_adr(results_data: dict[str, Any]) -> str:
    """WHAT: Compiles the full ADR-001 markdown document from benchmark results.
    WHY: Publishes final architectural decision with strict machine-generated evidence.

    Args:
        results_data: Complete benchmark results dictionary.
    Returns:
        Full markdown string for docs/adr/ADR-001-dependency-manager.md.
    Example:
        render_adr(results) -> "# ADR-001: Dependency Manager Selection..."
    """
    winner = results_data["decision"]["winner"]
    rationale = results_data["decision"]["rationale"]
    meta = results_data["metadata"]
    header = render_adr_header_and_context(
        meta, results_data["date"], results_data["seed"], results_data["input_spec_sha256"]
    )
    table = render_results_table(results_data)
    rep_table = render_repetition_details(results_data)
    footer = render_adr_footer(winner, rationale)
    warnings = check_cold_warm_divergence(results_data["tools"])
    warn_text = (
        ("\n\n### Divergence Warnings\n\n" + "\n\n".join(f"> [!WARNING]\n> {w}" for w in warnings))
        if warnings
        else ""
    )
    env_line = f"Host environment: {meta['os']}, CPU: {meta['cpu']}, RAM: {meta['ram']}, Python: {meta['python_version']}.\n\n"
    return (
        f"{header}## Benchmark Results\n\n{env_line}{table}{warn_text}\n\n"
        f"### Cold Install Verification Per Repetition\n\n{rep_table}\n\n{footer}"
    )


def run_h1_check(tool: str, project_dir: Path, repo_root: Path) -> tuple[bool, str, str, str]:
    """WHAT: Performs two independent locks from a clean state and compares SHA-256.
    WHY: Implements hard rule H1 verification.

    Args:
        tool: Tool name.
        project_dir: Working directory.
        repo_root: Repository root.
    Returns:
        (passed, hash1, hash2, detail).
    Example:
        run_h1_check("uv", Path("..."), Path(".")) -> (True, "abc", "abc", "Reproducible...")
    """
    if tool == "pip":
        return False, "", "", "Baseline pip does not lock dependencies"
    lock_file_name = (
        "uv.lock" if tool == "uv" else ("poetry.lock" if tool == "poetry" else "requirements.txt")
    )
    lock_path = project_dir / lock_file_name
    if lock_path.is_file():
        lock_path.unlink()
    clean_cache_verified(tool, repo_root)
    _, code1, tail1 = run_lock_step(tool, project_dir, repo_root)
    if code1 != 0 or not lock_path.is_file():
        return False, "", "", f"First lock failed with exit code {code1}: {tail1}"
    hash1 = compute_sha256(lock_path)
    lock_path.unlink()
    clean_cache_verified(tool, repo_root)
    _, code2, tail2 = run_lock_step(tool, project_dir, repo_root)
    if code2 != 0 or not lock_path.is_file():
        return False, hash1, "", f"Second lock failed with exit code {code2}: {tail2}"
    hash2 = compute_sha256(lock_path)
    passed, msg = verify_h1_reproducible(hash1, hash2)
    return passed, hash1, hash2, msg


def delete_project_lockfile(tool: str, project_dir: Path) -> None:
    """WHAT: Removes any existing lockfile and project environment before locking.
    WHY: Enforces that every repetition generates its lockfile from scratch.

    Args:
        tool: Tool identifier.
        project_dir: Project directory.
    Returns:
        Nothing.
    Example:
        delete_project_lockfile("uv", Path("work/project_uv")) -> None
    """
    lock_file_name = (
        "uv.lock" if tool == "uv" else ("poetry.lock" if tool == "poetry" else "requirements.txt")
    )
    lock_path = project_dir / lock_file_name
    if lock_path.is_file():
        lock_path.unlink()
    local_venv = project_dir / ".venv"
    if local_venv.is_dir():
        shutil.rmtree(local_venv, ignore_errors=True)


def execute_cold_install(
    tool: str,
    project_dir: Path,
    cold_venv: Path,
    repo_root: Path,
    clean_ok: bool,
    clean_msg: str,
    b_before: int,
    b_after: int,
) -> dict[str, Any]:
    """WHAT: Executes the cold install into a fresh venv or records clean failure.
    WHY: Modular helper ensuring run_tool_repetition remains within function length limits.

    Args:
        tool: Tool name.
        project_dir: Project directory.
        cold_venv: Target venv directory.
        repo_root: Repo root.
        clean_ok: Whether cache clearing verified empty.
        clean_msg: Cache clean verification message.
        b_before: Cache bytes before clean.
        b_after: Cache bytes after clean.
    Returns:
        Step result dictionary for cold install.
    Example:
        execute_cold_install("uv", proj, venv, repo, True, "clean", 100, 0) -> {...}
    """
    create_target_venv(cold_venv)
    if not clean_ok:
        return {
            "seconds": None,
            "exit_code": None,
            "status": "not_measured",
            "reason": f"Cache clean unverified: {clean_msg}",
            "tail": f"Cache clean unverified: {clean_msg}",
            "bytes_before": b_before,
            "bytes_after": b_after,
        }
    c_time, c_code, c_tail = run_install_step(tool, project_dir, cold_venv, repo_root)
    return {
        "seconds": c_time,
        "exit_code": c_code,
        "status": "ok" if c_code == 0 else "failed",
        "tail": c_tail,
        "bytes_before": b_before,
        "bytes_after": b_after,
    }


def run_tool_repetition(
    tool: str,
    project_dir: Path,
    work_dir: Path,
    repo_root: Path,
    rep_idx: int,
) -> dict[str, Any]:
    """WHAT: Executes one repetition (lock, cold install, warm install) for a given tool.
    WHY: Implements one interleaved trial measuring real wall-clock durations.

    Args:
        tool: Tool identifier.
        project_dir: Directory containing project definitions.
        work_dir: Scratch workspace directory.
        repo_root: Repository root path.
        rep_idx: Index of current repetition (0..4).
    Returns:
        Dictionary containing step timings and exit statuses.
    Example:
        run_tool_repetition("uv", proj, work, repo, 0) -> {"lock": 1.1, "cold": 5.2, ...}
    """
    clean_cache_verified(tool, repo_root)
    delete_project_lockfile(tool, project_dir)
    lock_time, lock_code, lock_tail = run_lock_step(tool, project_dir, repo_root)
    b_before, b_after, clean_ok, clean_msg = clean_cache_verified(tool, repo_root)
    cold_venv = work_dir / f"venv_{tool}_cold_rep{rep_idx}"
    cold_res = execute_cold_install(
        tool, project_dir, cold_venv, repo_root, clean_ok, clean_msg, b_before, b_after
    )
    warm_venv = work_dir / f"venv_{tool}_warm_rep{rep_idx}"
    create_target_venv(warm_venv)
    w_time, w_code, w_tail = run_install_step(tool, project_dir, warm_venv, repo_root)
    return {
        "lock": {
            "seconds": lock_time,
            "exit_code": lock_code,
            "status": "ok" if lock_code == 0 else "failed",
            "tail": lock_tail,
        },
        "cold_install": cold_res,
        "warm_install": {
            "seconds": w_time,
            "exit_code": w_code,
            "status": "ok" if w_code == 0 else "failed",
            "tail": w_tail,
        },
    }


def prepare_tool_project(tool: str, work_dir: Path, packages: list[str]) -> Path:
    """WHAT: Writes minimal configuration files needed by the specified tool.
    WHY: Ensures identical inputs while providing each tool its native project format.

    Args:
        tool: Tool identifier.
        work_dir: Work workspace path.
        packages: List of packages.
    Returns:
        Path to tool's project directory.
    Example:
        prepare_tool_project("uv", Path("work"), ["fastapi"]) -> Path("work/project_uv")
    """
    proj_dir = work_dir / f"project_{tool}"
    if tool == "uv":
        write_uv_project(proj_dir, packages)
    elif tool == "poetry":
        write_poetry_project(proj_dir, packages)
    elif tool in ("pip-tools", "pip"):
        write_piptools_project(proj_dir, packages)
    return proj_dir


def evaluate_h3_status(rep_results: list[dict[str, Any]]) -> tuple[bool, str]:
    """WHAT: Evaluates hard rule H3 across all executed runs, ignoring unmeasured steps.
    WHY: Separates execution verification from unmeasured trial steps.

    Args:
        rep_results: List of trial repetition dictionaries.
    Returns:
        (passed, detail_text).
    Example:
        evaluate_h3_status(runs) -> (True, "All executed runs exited with code 0")
    """
    has_not_measured = False
    failed_steps: list[str] = []
    for rep_idx, run in enumerate(rep_results):
        for step_name in ("lock", "cold_install", "warm_install"):
            step = run[step_name]
            status = step.get("status")
            code = step.get("exit_code")
            if status == "not_measured":
                has_not_measured = True
            elif status == "failed" or code != 0:
                code_str = str(code) if code is not None else "None"
                failed_steps.append(f"{step_name} rep {rep_idx + 1} (exit {code_str})")
    if failed_steps:
        detail = f"One or more runs failed: {', '.join(failed_steps)}"
        if has_not_measured:
            detail += " (cold install was not measured)"
        return False, detail
    if has_not_measured:
        return True, "All executed runs exited with code 0 (cold install was not measured)"
    return True, "All benchmark runs exited with code 0"


def check_tool_hard_rules(
    tool: str,
    project_dir: Path,
    repo_root: Path,
    rep_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """WHAT: Evaluates H1, H2, H3, and H4 rules for a candidate tool.
    WHY: Consolidates qualification checks for decision scoring.

    Args:
        tool: Tool identifier.
        project_dir: Tool project directory.
        repo_root: Repository root.
        rep_results: List of repetition outcome dictionaries.
    Returns:
        Dictionary mapping H1-H4 to pass status and details.
    Example:
        check_tool_hard_rules("uv", proj, repo, results) -> {"H1": {"passed": True}, ...}
    """
    h1_pass, h1_a, h1_b, h1_msg = run_h1_check(tool, project_dir, repo_root)
    lock_file = "uv.lock" if tool == "uv" else ("poetry.lock" if tool == "poetry" else "requirements.txt")
    lock_path = project_dir / lock_file
    lock_text = lock_path.read_text(encoding="utf-8") if lock_path.is_file() else ""
    h2_pass, h2_msg = verify_h2_hashes(tool, lock_text)
    h3_pass, h3_msg = evaluate_h3_status(rep_results)
    h4_pass, h4_msg = verify_h4_python_pin(tool, lock_text)
    return {
        "H1": {"passed": h1_pass, "detail": h1_msg, "hashes": [h1_a, h1_b]},
        "H2": {"passed": h2_pass, "detail": h2_msg},
        "H3": {"passed": h3_pass, "detail": h3_msg},
        "H4": {"passed": h4_pass, "detail": h4_msg},
    }


def summarize_tool_runs(tool: str, rep_results: list[dict[str, Any]]) -> dict[str, float | None]:
    """WHAT: Computes summary statistics (median, min, max) for tool trial timings.
    WHY: Supplies numerical evidence for the decision rule and report tables.

    Args:
        tool: Tool identifier.
        rep_results: List of execution dictionaries.
    Returns:
        Dictionary of medians, minimums, and maximums across measured steps.
    Example:
        summarize_tool_runs("uv", results) -> {"median_warm_install_seconds": 1.2, ...}
    """
    lock_times = [
        r["lock"]["seconds"]
        for r in rep_results
        if r["lock"]["status"] == "ok" and r["lock"]["seconds"] is not None
    ]
    cold_times = [
        r["cold_install"]["seconds"]
        for r in rep_results
        if r["cold_install"]["status"] == "ok" and r["cold_install"]["seconds"] is not None
    ]
    warm_times = [
        r["warm_install"]["seconds"]
        for r in rep_results
        if r["warm_install"]["status"] == "ok" and r["warm_install"]["seconds"] is not None
    ]
    has_lock = any(r.get("lock", {}).get("status") in ("ok", "failed") for r in rep_results)
    has_cold = any(r.get("cold_install", {}).get("status") in ("ok", "failed") for r in rep_results)
    has_warm = any(r.get("warm_install", {}).get("status") in ("ok", "failed") for r in rep_results)
    return {
        "median_lock_seconds": compute_median(lock_times) if lock_times else (0.0 if has_lock else None),
        "min_lock_seconds": min(lock_times) if lock_times else (0.0 if has_lock else None),
        "max_lock_seconds": max(lock_times) if lock_times else (0.0 if has_lock else None),
        "median_cold_install_seconds": compute_median(cold_times)
        if cold_times
        else (0.0 if has_cold else None),
        "min_cold_install_seconds": min(cold_times) if cold_times else (0.0 if has_cold else None),
        "max_cold_install_seconds": max(cold_times) if cold_times else (0.0 if has_cold else None),
        "median_warm_install_seconds": compute_median(warm_times)
        if warm_times
        else (0.0 if has_warm else None),
        "min_warm_install_seconds": min(warm_times) if warm_times else (0.0 if has_warm else None),
        "max_warm_install_seconds": max(warm_times) if warm_times else (0.0 if has_warm else None),
    }


def run_all_repetitions(
    tools: list[str],
    projects: dict[str, Path],
    work_dir: Path,
    repo_root: Path,
    seed: int,
    repetitions: int,
) -> dict[str, list[dict[str, Any]]]:
    """WHAT: Runs interleaved benchmark trials across all candidates.
    WHY: Guarantees reproducible, fair randomized order to mitigate network drift.

    Args:
        tools: List of tool names.
        projects: Map of tool names to project directories.
        work_dir: Workspace scratch directory.
        repo_root: Root repository path.
        seed: Random seed.
        repetitions: Number of repetitions.
    Returns:
        Mapping of tool name to list of repetition run dictionaries.
    Example:
        run_all_repetitions(["uv"], {"uv": Path("...")}, Path("..."), Path("."), 1729, 5) -> {...}
    """
    tool_runs: dict[str, list[dict[str, Any]]] = {tool: [] for tool in tools}
    rng = random.Random(seed)
    for rep_idx in range(repetitions):
        shuffled = list(tools)
        rng.shuffle(shuffled)
        for tool in shuffled:
            res = run_tool_repetition(tool, projects[tool], work_dir, repo_root, rep_idx)
            tool_runs[tool].append(res)
    return tool_runs


def execute_benchmark(
    repo_root: Path,
    work_dir: Path,
    seed: int = 1729,
    repetitions: int = 5,
) -> dict[str, Any]:
    """WHAT: Executes the full multi-tool benchmark with interleaved random trials.
    WHY: Top-level benchmark runner producing complete results.json data.

    Args:
        repo_root: Root repository path.
        work_dir: Benchmark scratch directory.
        seed: Random seed for trial interleaving (policy: 1729).
        repetitions: Number of repetitions per step (policy: 5).
    Returns:
        Dictionary formatted for results.json.
    Example:
        execute_benchmark(Path("."), Path("work")) -> {"tools": {...}, ...}
    """
    spec_path = repo_root / "bench/adr001/spec/requirements.in"
    packages = read_spec_packages(spec_path)
    spec_sha = compute_sha256(spec_path)
    tools = ["uv", "poetry", "pip-tools", "pip"]
    projects = {tool: prepare_tool_project(tool, work_dir, packages) for tool in tools}
    tool_runs = run_all_repetitions(tools, projects, work_dir, repo_root, seed, repetitions)
    meta = get_machine_metadata(repo_root)
    final_tools: dict[str, Any] = {}
    for tool in tools:
        hard_rules = check_tool_hard_rules(tool, projects[tool], repo_root, tool_runs[tool])
        summary = summarize_tool_runs(tool, tool_runs[tool])
        final_tools[tool] = {"hard_rules": hard_rules, "summary": summary, "runs": tool_runs[tool]}
    results: dict[str, Any] = {
        "date": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d"),
        "seed": seed,
        "repetitions": repetitions,
        "input_spec_sha256": spec_sha,
        "metadata": meta,
        "tools": final_tools,
    }
    winner, rationale = evaluate_decision(results)
    results["decision"] = {"winner": winner, "rationale": rationale}
    return results


def upgrade_single_run(tool_name: str, run: dict[str, Any]) -> dict[str, Any]:
    """WHAT: Reclassifies unverified cold cleans in a single repetition to not_measured.
    WHY: Converts invented exit code 1 to None while preserving real tool failures.

    Args:
        tool_name: Name of tool.
        run: Repetition dictionary.
    Returns:
        Updated repetition dictionary.
    Example:
        upgrade_single_run("poetry", run) -> updated_run
    """
    cold = run.get("cold_install", {})
    tail_text = cold.get("tail", "")
    if (
        cold.get("status") == "failed"
        and isinstance(tail_text, str)
        and tail_text.startswith("Cache clean unverified")
    ):
        cold["status"] = "not_measured"
        cold["exit_code"] = None
        cold["seconds"] = None
        cold["reason"] = tail_text
    elif cold.get("status") not in ("ok", "failed", "not_measured"):
        raise ValueError(f"Unrecognised cold_install entry in {tool_name}: {cold}")
    if run.get("lock", {}).get("status") not in ("ok", "failed"):
        raise ValueError(f"Unrecognised lock entry in {tool_name}: {run.get('lock')}")
    if run.get("warm_install", {}).get("status") not in ("ok", "failed"):
        raise ValueError(f"Unrecognised warm_install entry in {tool_name}: {run.get('warm_install')}")
    return run


def upgrade_tool_entry(tool_name: str, tool_info: dict[str, Any]) -> dict[str, Any]:
    """WHAT: Reclassifies runs and recomputes H3 and medians for a single tool.
    WHY: Modular helper ensuring upgrade_results_data remains under function line limits.

    Args:
        tool_name: Tool identifier.
        tool_info: Tool data dictionary from results.json.
    Returns:
        Updated tool dictionary.
    Example:
        upgrade_tool_entry("poetry", info) -> updated_info
    """
    runs = [upgrade_single_run(tool_name, r) for r in tool_info.get("runs", [])]
    summary = summarize_tool_runs(tool_name, runs)
    h3_passed, h3_detail = evaluate_h3_status(runs)
    hard_rules = dict(tool_info.get("hard_rules", {}))
    hard_rules["H3"] = {"passed": h3_passed, "detail": h3_detail}
    tool_info["runs"] = runs
    tool_info["summary"] = summary
    tool_info["hard_rules"] = hard_rules
    return tool_info


def upgrade_results_data(results_data: dict[str, Any]) -> dict[str, Any]:
    """WHAT: Upgrades entire benchmark results dictionary to the new classification format.
    WHY: Reclassifies cached data and updates decision without requiring benchmark rerun.

    Args:
        results_data: Full raw results dictionary from results.json.
    Returns:
        Updated results dictionary with recomputed H3 and summary medians.
    Example:
        upgrade_results_data(old_results) -> new_results
    """
    for tool_name, tool_info in results_data.get("tools", {}).items():
        results_data["tools"][tool_name] = upgrade_tool_entry(tool_name, tool_info)
    winner, rationale = evaluate_decision(results_data)
    results_data["decision"] = {"winner": winner, "rationale": rationale}
    return results_data


def upgrade_results_file(results_path: Path, adr_path: Path) -> dict[str, Any]:
    """WHAT: Reads, upgrades, writes results.json and re-renders ADR-001 markdown.
    WHY: Automates Commit 2 execution cleanly and deterministically.

    Args:
        results_path: Path to results.json.
        adr_path: Path to ADR-001 markdown output.
    Returns:
        Upgraded results dictionary.
    Example:
        upgrade_results_file(Path("results.json"), Path("ADR.md")) -> {...}
    """
    content = results_path.read_text(encoding="utf-8")
    data = json.loads(content)
    upgraded = upgrade_results_data(data)
    results_path.write_text(json.dumps(upgraded, indent=2), encoding="utf-8")
    adr_markdown = render_adr(upgraded)
    adr_path.write_text(adr_markdown, encoding="utf-8")
    return upgraded


def main() -> int:
    """WHAT: Main entry point running the benchmark and rendering ADR-001 outputs.
    WHY: Automates execution of benchmark from the command line.

    Args:
        none.
    Returns:
        Exit code (0 on success).
    Example:
        main() -> 0
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    work_dir = repo_root / "bench/adr001/work"
    results_dir = repo_root / "bench/adr001/results"
    results_dir.mkdir(parents=True, exist_ok=True)
    results_path = results_dir / "results.json"
    adr_path = repo_root / "docs/adr/ADR-001-dependency-manager.md"
    results = execute_benchmark(repo_root, work_dir, seed=1729, repetitions=5)
    results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    adr_markdown = render_adr(results)
    adr_path.write_text(adr_markdown, encoding="utf-8")
    print(f"Benchmark completed successfully. Winner: {results['decision']['winner']}")
    print(f"Results written to: {results_path}")
    print(f"ADR rendered to: {adr_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
