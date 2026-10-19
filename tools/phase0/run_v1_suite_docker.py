# TERM: container - an isolated Linux environment started from a Docker image.
# TERM: bind mount - a host folder made visible inside a container (docker run -v host:container).
# TERM: compose network - the private network Docker Compose creates so services reach each other by name.
# TERM: junit XML - a standard file pytest writes listing every test and its result.
"""Run v1's pytest suite inside a Linux python:3.11 container, matching v1's own Dockerfile.

Reuses the clone, parse, summary and writing steps of run_v1_suite.py. v1 tests and
requirements are never edited, skipped or filtered.
"""
from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from tools.phase0 import run_v1_suite as base

# sourced: tag name for the image built from tools/phase0/v1_baseline.Dockerfile.
IMAGE_NAME = "medora-v1-baseline:py311"
DOCKERFILE_PATH = Path(__file__).resolve().parent / "v1_baseline.Dockerfile"
ALLOWED_LABELS = ["docker-no-services", "docker-with-db"]
# sourced: Docker Compose names the default network "<folder>_default"; v1's folder is sentinel_flow_mvp.
COMPOSE_NETWORK = "sentinel_flow_mvp_default"
# assumption: building the small base image (apt only) takes well under 15 minutes.
BUILD_TIMEOUT_SECONDS = 900


def build_image() -> None:
    """WHAT: Builds the base image from v1_baseline.Dockerfile (python:3.11-slim-bookworm + apt packages).
    WHY: v1's own Dockerfile installs these system packages; tests should run in the same setting.

    Args:
        none.
    Returns:
        Nothing.
    Raises:
        RuntimeError: if docker build fails.
    Example:
        build_image() -> image "medora-v1-baseline:py311" exists
    """
    arguments = ["docker", "build", "-t", IMAGE_NAME, "-f", str(DOCKERFILE_PATH), str(DOCKERFILE_PATH.parent)]
    finished = base.run_command(arguments, None, BUILD_TIMEOUT_SECONDS)
    if finished.returncode != 0:
        raise RuntimeError(f"docker build failed: {finished.stderr.strip()[-2000:]}")


def container_arguments(project_dir: Path, out_dir: Path, network: str | None, command: list[str]) -> list[str]:
    """WHAT: Builds a `docker run` argument list that mounts v1's folder at /work and results at /out.
    WHY: One place defines how every container step is started.

    Args:
        project_dir: sentinel_flow_mvp folder on the host.
        out_dir: output folder on the host.
        network: compose network to join, or None.
        command: the program and arguments to run inside the container.
    Returns:
        The full argument list.
    Example:
        network=None, command=["python", "--version"] -> ["docker", "run", "--rm", ..., "python", "--version"]
    """
    arguments = ["docker", "run", "--rm", "-v", f"{project_dir.as_posix()}:/work",
                 "-v", f"{out_dir.resolve().as_posix()}:/out", "-w", "/work"]
    if network is not None:
        arguments += ["--network", network]
    return arguments + [IMAGE_NAME] + command


def container_pip_install(project_dir: Path, out_dir: Path, requirements_name: str, log_path: Path) -> base.InstallResult:
    """WHAT: Creates /work/.venv if missing, then runs pip install -r <file> inside the container.
    The full output is appended to the log; failure is returned as data.
    WHY: A failed install is a finding, and the venv keeps packages for the later pytest step.

    Args:
        project_dir: sentinel_flow_mvp folder.
        out_dir: output folder.
        requirements_name: file name inside project_dir.
        log_path: pip log to append to.
    Returns:
        InstallResult.
    Example:
        requirements.txt installs fine on Linux -> InstallResult(succeeded=True, return_code=0, ...)
    """
    venv_command = ["python", "-m", "venv", "/work/.venv"]
    base.run_command(container_arguments(project_dir, out_dir, None, venv_command), None, base.SHORT_COMMAND_TIMEOUT_SECONDS)
    pip_command = ["/work/.venv/bin/python", "-m", "pip", "install", "-r", requirements_name]
    arguments = container_arguments(project_dir, out_dir, None, pip_command)
    started = time.monotonic()
    timed_out = False
    try:
        finished = base.run_command(arguments, None, base.PIP_TIMEOUT_SECONDS)
        return_code, output = finished.returncode, finished.stdout + finished.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        return_code, output = -1, base.as_text(error.stdout) + base.as_text(error.stderr)
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"$ {' '.join(pip_command)}\nreturn code: {return_code}\n{output}\n")
    return base.InstallResult(return_code == 0, return_code, timed_out, time.monotonic() - started, log_path)


def container_run_pytest(project_dir: Path, out_dir: Path, run_label: str, network: str | None) -> base.RunResult:
    """WHAT: Runs the whole v1 suite inside the container with no filters.
    Only tests/benchmarks and tests/load are ignored; the pytest cache is off.
    WHY: Honest evidence - crashes and timeouts are recorded, never hidden.

    Args:
        project_dir: sentinel_flow_mvp folder.
        out_dir: output folder.
        run_label: label for this run.
        network: compose network to join, or None.
    Returns:
        RunResult.
    Side effects:
        Writes junit.xml, pytest.log, pytest-stderr.log into out_dir.
    Example:
        run_label="docker-no-services" -> RunResult(return_code=1, wall_seconds=95.2, ...)
    """
    command = ["/work/.venv/bin/python", "-m", "pytest", "-p", "no:cacheprovider", "-rA",
               "--junitxml=/out/junit.xml", "--ignore=tests/benchmarks", "--ignore=tests/load"]
    started = time.monotonic()
    timed_out = False
    try:
        finished = base.run_command(container_arguments(project_dir, out_dir, network, command), None, base.PYTEST_TIMEOUT_SECONDS)
        return_code, stdout, stderr = finished.returncode, finished.stdout, finished.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        return_code, stdout, stderr = -1, base.as_text(error.stdout), base.as_text(error.stderr)
    elapsed = time.monotonic() - started
    (out_dir / "pytest.log").write_text(stdout, encoding="utf-8")
    (out_dir / "pytest-stderr.log").write_text(stderr, encoding="utf-8")
    return base.RunResult(run_label, return_code, timed_out, elapsed, out_dir / "junit.xml",
                          out_dir / "pytest.log", out_dir / "pytest-stderr.log")


def container_python_version(project_dir: Path, out_dir: Path) -> str:
    """WHAT: Asks the container for its Python version string.
    WHY: Results must state exactly which Python produced them.

    Args:
        project_dir: sentinel_flow_mvp folder.
        out_dir: output folder.
    Returns:
        e.g. "Python 3.11.14".
    Example:
        container_python_version(proj, out) -> "Python 3.11.14"
    """
    command = ["python", "--version"]
    finished = base.run_command(container_arguments(project_dir, out_dir, None, command), None, base.SHORT_COMMAND_TIMEOUT_SECONDS)
    return finished.stdout.strip()


def record_container_outcome(
    args: argparse.Namespace, commit: str, project_dir: Path, out_dir: Path,
    result: base.RunResult | None, note: str,
) -> None:
    """WHAT: Parses junit.xml if present, summarises, and writes results.json/results.md.
    WHY: A failed install or crash must still leave honest evidence.

    Args:
        args: parsed command line.
        commit: v1 commit hash.
        project_dir: sentinel_flow_mvp folder.
        out_dir: output folder.
        result: RunResult or None if pytest never ran.
        note: finding text.
    Returns:
        Nothing.
    Example:
        junit.xml with 10 cases -> results.md lists 10 rows
    """
    rows: list[base.TestRow] = []
    sha, wall = "n/a", 0.0
    if result is not None:
        wall = result.wall_seconds
        if result.junit_path.exists():
            rows = base.parse_junit(result.junit_path)
            sha = base.sha256_of_file(result.junit_path)
        else:
            note = (note + " No junit XML was produced.").strip()
        if result.timed_out:
            note = (note + " pytest hit the timeout and was killed.").strip()
    os_name = f"Linux container (image {IMAGE_NAME}) on host {platform.system()} {platform.release()}"
    version = container_python_version(project_dir, out_dir)
    meta = base.RunMeta(args.tag, commit, args.run_label, datetime.now(timezone.utc).isoformat(),
                        os_name, version, round(wall, 1), sha, note)
    base.write_results(rows, base.summarise(rows), meta, out_dir)


def run_inside_container(args: argparse.Namespace, project_dir: Path, out_dir: Path) -> base.RunResult:
    """WHAT: Runs pytest in the container; for docker-with-db it first starts v1's timescaledb and redis
    and joins the container to their compose network, and always stops them afterwards (no -v).
    WHY: v1's settings reach Redis by the hostname "redis", which only works on that network.

    Args:
        args: parsed command line.
        project_dir: sentinel_flow_mvp folder.
        out_dir: output folder.
    Returns:
        RunResult.
    Raises:
        RuntimeError: if Docker or compose fails.
    Example:
        "docker-with-db" -> compose up timescaledb+redis, pytest on network, compose down
    """
    if args.run_label == "docker-no-services":
        return container_run_pytest(project_dir, out_dir, args.run_label, None)
    compose_file = project_dir / "docker-compose.yml"
    try:
        base.compose_command(compose_file, ["up", "-d", "--wait"] + base.DB_SERVICE_NAMES)
        return container_run_pytest(project_dir, out_dir, args.run_label, COMPOSE_NETWORK)
    finally:
        base.compose_command(compose_file, ["down"])


def execute_container_run(args: argparse.Namespace, work_root: Path, out_dir: Path) -> int:
    """WHAT: Runs every step: check Docker, build image, clone, install, pytest, record.
    WHY: Keeps main() short and the order of steps visible.

    Args:
        args: parsed command line.
        work_root: new temp folder.
        out_dir: output folder.
    Returns:
        0 on completion (test failures are results).
    Raises:
        RuntimeError: when Docker is unavailable or a required step fails.
    Example:
        --run-label docker-no-services -> writes docs/.../docker-no-services/*, returns 0
    """
    if not base.docker_available():
        raise RuntimeError("Docker is not available")
    build_image()
    project_dir = base.clone_v1(args.tag, work_root / "v1")
    commit = base.read_commit_hash(work_root / "v1")
    shutil.copy(Path(__file__).resolve().parents[2] / "requirements-dev.txt", project_dir / "requirements-dev.txt")
    pip_log = out_dir / "pip.log"
    pip_log.unlink(missing_ok=True)
    installed = container_pip_install(project_dir, out_dir, "requirements.txt", pip_log)
    if installed.succeeded:
        installed = container_pip_install(project_dir, out_dir, "requirements-dev.txt", pip_log)
    note = "" if installed.succeeded else f"pip install failed (rc={installed.return_code}); tests not run."
    result = run_inside_container(args, project_dir, out_dir) if installed.succeeded else None
    record_container_outcome(args, commit, project_dir, out_dir, result, note)
    for log_name in ["pip.log", "pytest.log", "pytest-stderr.log"]:
        base.trim_log_if_large(out_dir / log_name)
    return 0


def main(argv: list[str]) -> int:
    """WHAT: Entry point: validates the label, makes a fresh temp folder, runs, then cleans up.
    WHY: The finally block removes the temp clone even on errors; results are already in out_dir.

    Args:
        argv: command-line arguments.
    Returns:
        Exit code.
    Example:
        main(["--run-label", "docker-no-services"]) -> 0
    """
    parser = argparse.ArgumentParser(description="Record v1's test results inside a Linux container.")
    parser.add_argument("--run-label", required=True, choices=ALLOWED_LABELS)
    parser.add_argument("--tag", default=base.V1_DEFAULT_TAG)
    parser.add_argument("--out-root", default="docs/phase0/v1-test-results")
    args = parser.parse_args(argv)
    out_dir = Path(args.out_root) / args.run_label
    out_dir.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix="v1_suite_docker_"))
    try:
        return execute_container_run(args, work_root, out_dir)
    finally:
        shutil.rmtree(work_root, onerror=base.make_writable_and_retry)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
