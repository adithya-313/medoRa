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
import json
import platform
import re
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
# assumption: building the small base image (apt only) takes well under 15 minutes.
BUILD_TIMEOUT_SECONDS = 900
# reported: the first docker run downloaded about 3.6 GB in 3600 s and was still downloading when the
# 1 h ceiling killed it; 4 h gives headroom at this laptop's speed (assumption, not a v1 fact).
CONTAINER_PIP_TIMEOUT_SECONDS = 14400
# assumption: removing a cloned folder inside a container finishes within 10 minutes.
CLEANUP_TIMEOUT_SECONDS = 600
# a named Docker volume that keeps downloaded wheels between runs (a download cache, not a venv).
PIP_CACHE_VOLUME = "medora-v1-pip-cache"


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


def container_arguments(
    project_dir: Path, out_dir: Path, network: str | None, command: list[str], name: str,
    use_cache: bool = False,
) -> list[str]:
    """WHAT: Builds a `docker run` argument list that mounts v1's folder at /work and results at /out.
    WHY: One place defines how every container step is started.

    Args:
        project_dir: sentinel_flow_mvp folder on the host.
        out_dir: output folder on the host.
        network: compose network to join, or None.
        command: the program and arguments to run inside the container.
        name: container name, so a timed-out container can be removed.
        use_cache: mount the wheel-cache volume (only pip install should).
    Returns:
        The full argument list.
    Example:
        network=None, command=["python", "--version"] -> ["docker", "run", "--rm", ..., "python", "--version"]
    """
    arguments = ["docker", "run", "--rm", "-v", f"{project_dir.as_posix()}:/work",
                 "-v", f"{out_dir.resolve().as_posix()}:/out", "--name", name, "-w", "/work"]
    if use_cache:
        arguments += ["-v", f"{PIP_CACHE_VOLUME}:/root/.cache/pip"]
    if network is not None:
        arguments += ["--network", network]
    return arguments + [IMAGE_NAME] + command


# sourced: pip prints sizes in decimal units (1 kB = 1000 bytes), e.g. "Downloading x.whl (53.9 MB)".
BYTES_PER_UNIT = {"kB": 1000, "MB": 1000 * 1000, "GB": 1000 * 1000 * 1000}
PIP_SIZE_PATTERN = re.compile(r"^(Downloading|Using cached) \S+ \(([0-9.]+) (kB|MB|GB)\)", re.MULTILINE)


def parse_pip_sizes(log_text: str) -> tuple[int, int]:
    """WHAT: Adds up the file sizes pip printed, split into freshly downloaded and served from cache.
    WHY: The huge CUDA/torch download is a real v1 finding, so its size is recorded from the log.

    Args:
        log_text: full pip log.
    Returns:
        (downloaded_bytes, cached_bytes).
    Example:
        "Downloading a.whl (1.5 MB)" and "Using cached b.whl (200 kB)" -> (1500000, 200000)
    """
    downloaded_bytes = 0
    cached_bytes = 0
    for action, number, unit in PIP_SIZE_PATTERN.findall(log_text):
        size_bytes = int(float(number) * BYTES_PER_UNIT[unit])
        if action == "Downloading":
            downloaded_bytes += size_bytes
        else:
            cached_bytes += size_bytes
    return downloaded_bytes, cached_bytes


def image_id() -> str:
    """WHAT: Reads the local image ID (sha256) of the baseline image.
    WHY: A locally built image has no registry digest; its ID identifies exactly what ran.

    Args:
        none.
    Returns:
        e.g. "sha256:ab12...".
    Raises:
        RuntimeError: if docker image inspect fails.
    Example:
        image_id() -> "sha256:4f1c..."
    """
    arguments = ["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE_NAME]
    finished = base.run_command(arguments, None, base.SHORT_COMMAND_TIMEOUT_SECONDS)
    if finished.returncode != 0:
        raise RuntimeError(f"docker image inspect failed: {finished.stderr.strip()}")
    return finished.stdout.strip()


def cache_state() -> str:
    """WHAT: Reports whether the wheel-cache volume is absent, empty or reused (with its size).
    WHY: A run on a warm cache is not a cold install; the results must say which it was.

    Args:
        none.
    Returns:
        "absent", "empty" or "reused (<N> kB)".
    Example:
        volume holding 3 GB of wheels -> "reused (3000000 kB)"
    """
    inspect_arguments = ["docker", "volume", "inspect", PIP_CACHE_VOLUME]
    if base.run_command(inspect_arguments, None, base.SHORT_COMMAND_TIMEOUT_SECONDS).returncode != 0:
        return "absent"
    probe = ["docker", "run", "--rm", "-v", f"{PIP_CACHE_VOLUME}:/cache", IMAGE_NAME, "du", "-sk", "/cache"]
    finished = base.run_command(probe, None, base.SHORT_COMMAND_TIMEOUT_SECONDS)
    size_kilobytes = int(finished.stdout.split()[0]) if finished.returncode == 0 else -1
    # an empty directory still reports a few kB for the directory entry itself
    return "empty" if 0 <= size_kilobytes <= 8 else f"reused ({size_kilobytes} kB)"


SIZE_MEASUREMENT_NOTE = (
    "ESTIMATE: sums the sizes pip prints on 'Downloading <file> (<size>)' and 'Using cached <file> (<size>)' "
    "log lines; pip rounds to 3 significant digits and skips files whose size it does not print."
)


def cache_label(cache_before: str) -> str:
    """WHAT: Names the install condition: "warm cache" when the wheel volume was reused, else cold.
    WHY: Install time and size only mean something together with the cache state.

    Args:
        cache_before: value from cache_state(), or the skipped text.
    Returns:
        "warm cache" or "cold install".
    Example:
        "reused (1500000 kB)" -> "warm cache"; "absent" -> "cold install"
    """
    return "warm cache" if cache_before.startswith("reused") else "cold install"


def install_note(cache_before: str, wall_seconds: float, downloaded: int, cached: int) -> str:
    """WHAT: Builds the sentence put into results.md about install time and size.
    WHY: A reader of results.md must see that the numbers are warm-cache numbers.

    Args:
        cache_before: cache state text.
        wall_seconds: pip seconds for v1's requirements.txt.
        downloaded: bytes pip downloaded.
        cached: bytes pip took from the cache.
    Returns:
        One sentence.
    Example:
        ("reused (9 kB)", 300.0, 1000000, 2000000) -> "Install ran on a warm cache: pip 300.0 s, ..."
    """
    return (f"Install ran on a {cache_label(cache_before)}: pip {round(wall_seconds, 1)} s, "
            f"downloaded {round(downloaded / 1_000_000, 1)} MB, served from cache {round(cached / 1_000_000, 1)} MB "
            f"(sizes are an ESTIMATE from pip log lines; not a cold-install figure unless labelled cold).")


def write_meta_json(out_dir: Path, fields: dict[str, object]) -> None:
    """WHAT: Writes meta.json with the container-run facts.
    WHY: Results.json holds test data; meta.json holds how the environment was built.

    Args:
        out_dir: output folder.
        fields: key/value facts.
    Returns:
        Nothing.
    Example:
        {"pip_exit_code": 0} -> out_dir/meta.json
    """
    (out_dir / "meta.json").write_text(json.dumps(fields, indent=2), encoding="utf-8")


def stop_container(name: str) -> None:
    """WHAT: Force-removes a named container.
    WHY: Killing only the docker client on a timeout leaves the container (and pip) running on.

    Args:
        name: container name.
    Returns:
        Nothing.
    Example:
        stop_container("medora-v1-pip") -> container stopped and removed
    """
    base.run_command(["docker", "rm", "-f", name], None, base.SHORT_COMMAND_TIMEOUT_SECONDS)


def remove_clone_in_container(work_root: Path) -> None:
    """WHAT: Deletes the cloned v1 folder from inside a container.
    WHY: The Linux venv has symlinks (lib64) that Windows cannot delete, which broke cleanup.

    Args:
        work_root: temp folder that holds the "v1" clone.
    Returns:
        Nothing.
    Example:
        work_root with v1/.venv/lib64 -> v1 folder removed
    """
    if not (work_root / "v1").exists():
        return
    arguments = ["docker", "run", "--rm", "-v", f"{work_root.as_posix()}:/cleanup", IMAGE_NAME, "rm", "-rf", "/cleanup/v1"]
    base.run_command(arguments, None, CLEANUP_TIMEOUT_SECONDS)


def container_pip_install(
    project_dir: Path, out_dir: Path, requirements_name: str, log_path: Path, use_cache: bool
) -> base.InstallResult:
    """WHAT: Creates /work/.venv if missing, then runs pip install -r <file> inside the container.
    The full output is appended to the log; failure is returned as data.
    WHY: A failed install is a finding, and the venv keeps packages for the later pytest step.

    Args:
        project_dir: sentinel_flow_mvp folder.
        out_dir: output folder.
        requirements_name: file name inside project_dir.
        log_path: pip log to append to.
        use_cache: mount the wheel-cache volume (False for --fresh-cache).
    Returns:
        InstallResult.
    Example:
        requirements.txt installs fine on Linux -> InstallResult(succeeded=True, return_code=0, ...)
    """
    venv_command = ["python", "-m", "venv", "/work/.venv"]
    base.run_command(container_arguments(project_dir, out_dir, None, venv_command, "medora-v1-venv"), None, base.SHORT_COMMAND_TIMEOUT_SECONDS)
    pip_command = ["/work/.venv/bin/python", "-m", "pip", "install", "-r", requirements_name]
    arguments = container_arguments(project_dir, out_dir, None, pip_command, "medora-v1-pip", use_cache)
    started = time.monotonic()
    timed_out = False
    try:
        finished = base.run_command(arguments, None, CONTAINER_PIP_TIMEOUT_SECONDS)
        return_code, output = finished.returncode, finished.stdout + finished.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        stop_container("medora-v1-pip")
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
        finished = base.run_command(container_arguments(project_dir, out_dir, network, command, "medora-v1-pytest"), None, base.PYTEST_TIMEOUT_SECONDS)
        return_code, stdout, stderr = finished.returncode, finished.stdout, finished.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        stop_container("medora-v1-pytest")
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
    finished = base.run_command(container_arguments(project_dir, out_dir, None, command, "medora-v1-version"), None, base.SHORT_COMMAND_TIMEOUT_SECONDS)
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


def discover_compose_network(compose_file: Path, out_dir: Path) -> str:
    """WHAT: Finds the network the started redis container is really on, and checks it against
    the real `docker network ls` output (saved to docker-network-ls.txt and printed).
    WHY: The name is never assumed; it is read from Docker after compose up.

    Args:
        compose_file: v1's docker-compose.yml.
        out_dir: output folder for the saved listing.
    Returns:
        The network name.
    Raises:
        RuntimeError: if redis is not running, is on a different number of networks than one,
            or the network is missing from `docker network ls`.
    Example:
        redis on "sentinel_flow_mvp_default" -> "sentinel_flow_mvp_default"
    """
    timeout = base.SHORT_COMMAND_TIMEOUT_SECONDS
    listing = base.run_command(["docker", "network", "ls"], None, timeout).stdout
    (out_dir / "docker-network-ls.txt").write_text(listing, encoding="utf-8")
    print(listing)
    ps_arguments = ["docker", "compose", "-f", str(compose_file), "ps", "-q", "redis"]
    container_id = base.run_command(ps_arguments, compose_file.parent, timeout).stdout.strip()
    inspect_arguments = ["docker", "inspect", "--format", "{{range $name, $net := .NetworkSettings.Networks}}{{$name}} {{end}}", container_id]
    names = base.run_command(inspect_arguments, None, timeout).stdout.split()
    if container_id == "" or len(names) != 1 or names[0] not in listing.split():
        raise RuntimeError(f"cannot determine the compose network (networks seen: {names})")
    return names[0]


def run_inside_container(args: argparse.Namespace, project_dir: Path, out_dir: Path) -> base.RunResult:
    """WHAT: Runs pytest in the container; for docker-with-db it first starts v1's timescaledb and redis
    and joins the container to their compose network, and always stops them afterwards (no -v).
    WHY: v1's settings reach Redis by the hostname "redis", which only works on that network;
    the network name is read from Docker, not assumed.

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
        network = discover_compose_network(compose_file, out_dir)
        return container_run_pytest(project_dir, out_dir, args.run_label, network)
    finally:
        base.compose_command(compose_file, ["down"])


def install_and_record_meta(
    args: argparse.Namespace, project_dir: Path, out_dir: Path, pip_log: Path
) -> tuple[base.InstallResult, str]:
    """WHAT: Installs v1's requirements, then the dev requirements, and writes meta.json.
    WHY: Install facts (image, cache state, seconds, sizes) are recorded even when pip fails.

    Args:
        args: parsed command line.
        project_dir: sentinel_flow_mvp folder.
        out_dir: output folder.
        pip_log: pip log path.
    Returns:
        (last InstallResult, note text for results.md).
    Example:
        install ok on a warm cache -> (InstallResult(succeeded=True, ...), "Install ran on a warm cache: ...")
    """
    use_cache = not args.fresh_cache
    cache_before = cache_state() if use_cache else "skipped (--fresh-cache)"
    v1_install = container_pip_install(project_dir, out_dir, "requirements.txt", pip_log, use_cache)
    installed = v1_install
    if installed.succeeded:
        installed = container_pip_install(project_dir, out_dir, "requirements-dev.txt", pip_log, use_cache)
    downloaded, cached = parse_pip_sizes(pip_log.read_text(encoding="utf-8", errors="replace"))
    write_meta_json(out_dir, {
        "image_name": IMAGE_NAME, "image_id": image_id(), "cache_before_run": cache_before,
        "fresh_cache": args.fresh_cache, "install_cache_label": cache_label(cache_before),
        "pip_exit_code": v1_install.return_code, "pip_wall_seconds": round(v1_install.wall_seconds, 1),
        "pip_timed_out": v1_install.timed_out, "pip_downloaded_bytes": downloaded,
        "pip_cached_bytes": cached, "size_measurement": SIZE_MEASUREMENT_NOTE,
    })
    note = install_note(cache_before, v1_install.wall_seconds, downloaded, cached)
    if not installed.succeeded:
        note += f" pip install failed (rc={installed.return_code}); tests not run."
    return installed, note


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
    installed, note = install_and_record_meta(args, project_dir, out_dir, pip_log)
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
    parser.add_argument("--fresh-cache", action="store_true", help="do not mount the wheel-cache volume (cold install)")
    parser.add_argument("--tag", default=base.V1_DEFAULT_TAG)
    parser.add_argument("--out-root", default="docs/phase0/v1-test-results")
    args = parser.parse_args(argv)
    out_dir = Path(args.out_root) / args.run_label
    out_dir.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix="v1_suite_docker_"))
    try:
        return execute_container_run(args, work_root, out_dir)
    finally:
        remove_clone_in_container(work_root)
        shutil.rmtree(work_root, onerror=base.make_writable_and_retry)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
