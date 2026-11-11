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
from datetime import UTC, datetime
from pathlib import Path

from tools.phase0 import run_v1_suite as base

# sourced: tag name for the image built from tools/phase0/v1_baseline.Dockerfile.
IMAGE_NAME = "medora-v1-baseline:py311"
DOCKERFILE_PATH = Path(__file__).resolve().parent / "v1_baseline.Dockerfile"
ALLOWED_LABELS = ["docker-no-services", "docker-with-db", "docker-with-db-health-risk"]
# assumption: building the small base image (apt only) takes well under 15 minutes.
BUILD_TIMEOUT_SECONDS = 900
# reported: the first docker run downloaded about 3.6 GB in 3600 s and was still downloading when the
# 1 h ceiling killed it; 4 h gives headroom at this laptop's speed (assumption, not a v1 fact).
CONTAINER_PIP_TIMEOUT_SECONDS = 14400
# policy: 9.9 GB image save - one hour ceiling for docker commit (the 10-minute limit was too short in the
# first with-db attempt; raised with the owner's approval).
COMMIT_TIMEOUT_SECONDS = 3600
# assumption: removing a cloned folder inside a container finishes within 10 minutes.
CLEANUP_TIMEOUT_SECONDS = 600
# policy: the owner asked for a unique compose project name so "down" only touches what this script made.
COMPOSE_PROJECT_NAME = "medora-v1-baseline"
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
    use_cache: bool = False, image: str = IMAGE_NAME, environment: dict[str, str] | None = None,
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
        image: image to run.
        environment: variables to pass with -e (None for none).
    Returns:
        The full argument list.
    Example:
        network=None, command=["python", "--version"] -> ["docker", "run", "--rm", ..., "python", "--version"]
    """
    arguments = ["docker", "run", "--rm", "-v", f"{project_dir.as_posix()}:/work",
                 "-v", f"{out_dir.resolve().as_posix()}:/out", "--name", name, "-w", "/work"]
    if use_cache:
        arguments += ["-v", f"{PIP_CACHE_VOLUME}:/root/.cache/pip"]
    for key, value in (environment or {}).items():
        arguments += ["-e", f"{key}={value}"]
    if network is not None:
        arguments += ["--network", network]
    return arguments + [image] + command


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


def image_id(image: str = IMAGE_NAME) -> str:
    """WHAT: Reads the local image ID (sha256) of the baseline image.
    WHY: A locally built image has no registry digest; its ID identifies exactly what ran.

    Args:
        image: image name or tag.
    Returns:
        e.g. "sha256:ab12...".
    Raises:
        RuntimeError: if docker image inspect fails.
    Example:
        image_id() -> "sha256:4f1c..."
    """
    arguments = ["docker", "image", "inspect", "--format", "{{.Id}}", image]
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


def pytest_command(python_command: str, test_path: str) -> list[str]:
    """WHAT: Builds the pytest command line used inside the container: cache off, full report, junit XML,
    the two script folders ignored, and one explicit test path. No -k, -x or other filters.
    WHY: One place defines the arguments so every run uses exactly the same ones; only the path differs.

    Args:
        python_command: python inside the container.
        test_path: path (relative to v1's sentinel_flow_mvp folder) that pytest should collect.
    Returns:
        The argument list.
    Example:
        ("python", "tests") -> ["python", "-m", "pytest", "-p", "no:cacheprovider", "-rA", ..., "tests"]
    """
    return [python_command, "-m", "pytest", "-p", "no:cacheprovider", "-rA", "--junitxml=/out/junit.xml",
            "--ignore=tests/benchmarks", "--ignore=tests/load", test_path]


def container_run_pytest(
    project_dir: Path, out_dir: Path, run_label: str, network: str | None,
    python_command: str = "/work/.venv/bin/python", image: str = IMAGE_NAME,
    environment: dict[str, str] | None = None, test_path: str = "tests",
) -> base.RunResult:
    """WHAT: Runs the whole v1 suite inside the container with no filters.
    Only tests/benchmarks and tests/load are ignored; the pytest cache is off.
    The plain path "tests" makes pytest find v1's own tests/pytest.ini (approved by the owner).
    WHY: Honest evidence - crashes and timeouts are recorded, never hidden.

    Args:
        project_dir: sentinel_flow_mvp folder.
        out_dir: output folder.
        run_label: label for this run.
        network: compose network to join, or None.
        python_command: python inside the container (the venv one, or "python" in the env image).
        image: image to run.
        environment: variables passed to the container (e.g. DATABASE_URL).
        test_path: path that pytest collects (default: v1's tests folder).
    Returns:
        RunResult.
    Side effects:
        Writes junit.xml, pytest.log, pytest-stderr.log into out_dir.
    Example:
        run_label="docker-no-services" -> RunResult(return_code=1, wall_seconds=95.2, ...)
    """
    command = pytest_command(python_command, test_path)
    started = time.monotonic()
    timed_out = False
    try:
        finished = base.run_command(container_arguments(project_dir, out_dir, network, command, "medora-v1-pytest", False, image, environment), None, base.PYTEST_TIMEOUT_SECONDS)
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
    os_name = f"Linux container (image {getattr(args, "image_name", IMAGE_NAME)}) on host {platform.system()} {platform.release()}"
    version = container_python_version(project_dir, out_dir)
    meta = base.RunMeta(args.tag, commit, args.run_label, datetime.now(UTC).isoformat(),
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
    ps_arguments = ["docker", "compose", "-p", COMPOSE_PROJECT_NAME, "-f", str(compose_file), "ps", "-q", "redis"]
    container_id = base.run_command(ps_arguments, compose_file.parent, timeout).stdout.strip()
    inspect_arguments = ["docker", "inspect", "--format", "{{range $name, $net := .NetworkSettings.Networks}}{{$name}} {{end}}", container_id]
    names = base.run_command(inspect_arguments, None, timeout).stdout.split()
    if container_id == "" or len(names) != 1 or names[0] not in listing.split():
        raise RuntimeError(f"cannot determine the compose network (networks seen: {names})")
    return names[0]


# sourced: compose "!reset" (Compose v2.24+) clears a key. Host ports 5432/6379 and the fixed container names
# sentinel_db/sentinel_redis are already taken by other containers on this laptop.
PORT_OVERRIDE_TEXT = (
    "services:" + chr(10)
    + "  timescaledb:" + chr(10) + "    container_name: !reset null" + chr(10) + "    ports: !reset []" + chr(10)
    + "  redis:" + chr(10) + "    container_name: !reset null" + chr(10) + "    ports: !reset []" + chr(10)
)


def compose_with_override(compose_file: Path, override_file: Path, action: list[str]) -> None:
    """WHAT: Runs docker compose with v1's file plus an override that removes published host ports.
    WHY: Host ports 5432 and 6379 belong to other containers on this laptop; the tests run inside
    the compose network, so no host port is needed. v1's own compose file is left untouched.
    It also copies .env.example to .env only while compose runs (compose requires the file).

    Args:
        compose_file: v1's docker-compose.yml.
        override_file: the override file.
        action: e.g. ["up", "-d", "--wait", "redis"] or ["down"].
    Returns:
        Nothing.
    Raises:
        RuntimeError: on a non-zero return code.
    Example:
        action=["down"] -> containers stopped, volumes kept
    """
    arguments = ["docker", "compose", "-p", COMPOSE_PROJECT_NAME, "-f", str(compose_file), "-f", str(override_file)] + action
    # v1's api/worker services declare "env_file: .env", and compose refuses to load without it.
    # The file exists only while compose runs, so pytest never sees it (pydantic would read it).
    env_file = compose_file.parent / ".env"
    shutil.copy(compose_file.parent / ".env.example", env_file)
    try:
        finished = base.run_command(arguments, compose_file.parent, base.DOCKER_TIMEOUT_SECONDS)
    finally:
        env_file.unlink(missing_ok=True)
    if finished.returncode != 0:
        raise RuntimeError(f"docker compose {action[0]} failed: {finished.stderr.strip()}")


# sourced: the owner asked for the committed environment image to be tagged v1-baseline-env.
ENV_IMAGE_NAME = "v1-baseline-env"
ENV_CONTAINER_NAME = "medora-v1-env-build"
# the earlier no-services run's pip log, used to compare package versions with the new image.
NO_SERVICES_PIP_LOG = "docker-no-services/pip.log"


def normalise_name(name: str) -> str:
    """WHAT: Lower-cases a package name and turns runs of '-', '_' and '.' into one '-'.
    WHY: pip freeze and pip logs spell the same package differently (e.g. "Pillow", "typing_extensions").

    Args:
        name: package name as printed by pip.
    Returns:
        The normalised name.
    Example:
        "Typing_Extensions" -> "typing-extensions"
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_freeze(freeze_text: str) -> dict[str, str]:
    """WHAT: Reads `pip freeze` output (name==version lines) into a name -> version map.
    WHY: Package lists must be compared by value, not by eye.

    Args:
        freeze_text: output of pip freeze.
    Returns:
        Map of normalised name to version; lines without '==' are ignored.
    Example:
        "torch==2.12.0" -> {"torch": "2.12.0"}
    """
    packages: dict[str, str] = {}
    for line in freeze_text.splitlines():
        if "==" in line:
            name, version = line.strip().split("==", 1)
            packages[normalise_name(name)] = version
    return packages


def parse_installed_from_log(log_text: str) -> dict[str, str]:
    """WHAT: Reads every "Successfully installed name-version ..." line of a pip log.
    WHY: The earlier run kept only its pip log (its venv was deleted), so the log is its package list.

    Args:
        log_text: full pip log.
    Returns:
        Map of normalised name to version.
    Example:
        "Successfully installed torch-2.12.0 numpy-2.1.0" -> {"torch": "2.12.0", "numpy": "2.1.0"}
    """
    packages: dict[str, str] = {}
    for line in log_text.splitlines():
        if line.startswith("Successfully installed "):
            for token in line.split()[2:]:
                name, version = token.rsplit("-", 1)
                packages[normalise_name(name)] = version
    return packages


def compare_packages(image_packages: dict[str, str], venv_packages: dict[str, str]) -> dict[str, object]:
    """WHAT: Compares the new image's packages with the earlier run's packages.
    WHY: Any version difference between the two environments must be reported, never hidden.

    Args:
        image_packages: from pip freeze in the new image.
        venv_packages: from the earlier run's pip log.
    Returns:
        Dict with only_in_image, only_in_earlier_run and version_differences (name, image, earlier).
    Example:
        image {"a": "1", "pip": "24"}, earlier {"a": "2"} -> only_in_image ["pip==24"], version_differences [["a","1","2"]]
    """
    only_in_image = [f"{name}=={image_packages[name]}" for name in sorted(set(image_packages) - set(venv_packages))]
    only_in_earlier = [f"{name}=={venv_packages[name]}" for name in sorted(set(venv_packages) - set(image_packages))]
    differences = []
    for name in sorted(set(image_packages) & set(venv_packages)):
        if image_packages[name] != venv_packages[name]:
            differences.append([name, image_packages[name], venv_packages[name]])
    return {"only_in_image": only_in_image, "only_in_earlier_run": only_in_earlier,
            "version_differences": differences}


def start_env_container(project_dir: Path) -> None:
    """WHAT: Starts an idle container from the base image with only v1's requirement files (read-only)
    and the wheel-cache volume mounted. Packages will be installed into its own filesystem.
    WHY: This is the one change from the earlier runs: where the environment lives (container
    filesystem instead of the Windows bind mount). The install command itself stays the same.

    Args:
        project_dir: sentinel_flow_mvp folder of a fresh clone.
    Returns:
        Nothing.
    Raises:
        RuntimeError: if docker run fails.
    Example:
        start_env_container(proj) -> container "medora-v1-env-build" running
    """
    reqs = (project_dir / "requirements.txt").as_posix()
    dev_reqs = (project_dir / "requirements-dev.txt").as_posix()
    arguments = ["docker", "run", "-d", "--name", ENV_CONTAINER_NAME,
                 "-v", f"{reqs}:/tmp/requirements.txt:ro", "-v", f"{dev_reqs}:/tmp/requirements-dev.txt:ro",
                 "-v", f"{PIP_CACHE_VOLUME}:/root/.cache/pip", IMAGE_NAME, "sleep", "infinity"]
    finished = base.run_command(arguments, None, base.SHORT_COMMAND_TIMEOUT_SECONDS)
    if finished.returncode != 0:
        raise RuntimeError(f"cannot start the build container: {finished.stderr.strip()}")


def exec_pip(requirements_path: str, log_path: Path) -> base.InstallResult:
    """WHAT: Runs `python -m pip install -r <file>` inside the build container and appends the output to a log.
    WHY: Same pip command as the earlier runs; only the install location differs. Timed as FACT.

    Args:
        requirements_path: path of the requirements file inside the container.
        log_path: pip log to append to.
    Returns:
        InstallResult with return code and wall-clock seconds.
    Example:
        exec_pip("/tmp/requirements.txt", log) -> InstallResult(succeeded=True, wall_seconds=900.0, ...)
    """
    command = ["python", "-m", "pip", "install", "-r", requirements_path]
    started = time.monotonic()
    timed_out = False
    try:
        finished = base.run_command(["docker", "exec", ENV_CONTAINER_NAME] + command, None, CONTAINER_PIP_TIMEOUT_SECONDS)
        return_code, output = finished.returncode, finished.stdout + finished.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        return_code, output = -1, base.as_text(error.stdout) + base.as_text(error.stderr)
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"$ {' '.join(command)}\nreturn code: {return_code}\n{output}\n")
    return base.InstallResult(return_code == 0, return_code, timed_out, time.monotonic() - started, log_path)


def freeze_and_commit(out_dir: Path) -> tuple[str, str, float]:
    """WHAT: Saves `pip freeze` from the build container, then commits the container as the image v1-baseline-env.
    WHY: The freeze (with its SHA-256) pins exactly which packages the with-db run used.

    Args:
        out_dir: output folder that receives pip-freeze.txt.
    Returns:
        (SHA-256 of pip-freeze.txt, image ID of v1-baseline-env, seconds docker commit took).
    Raises:
        RuntimeError: if pip freeze or docker commit fails.
    Example:
        freeze_and_commit(out) -> ("9f2c...", "sha256:73e7...", 412.6)
    """
    timeout = base.SHORT_COMMAND_TIMEOUT_SECONDS
    freeze = base.run_command(["docker", "exec", ENV_CONTAINER_NAME, "python", "-m", "pip", "freeze"], None, timeout)
    if freeze.returncode != 0:
        raise RuntimeError(f"pip freeze failed: {freeze.stderr.strip()}")
    (out_dir / "pip-freeze.txt").write_text(freeze.stdout, encoding="utf-8")
    commit_started = time.monotonic()
    commit = base.run_command(["docker", "commit", ENV_CONTAINER_NAME, ENV_IMAGE_NAME], None, COMMIT_TIMEOUT_SECONDS)
    commit_seconds = time.monotonic() - commit_started
    if commit.returncode != 0:
        raise RuntimeError(f"docker commit failed: {commit.stderr.strip()}")
    return base.sha256_of_file(out_dir / "pip-freeze.txt"), image_id(ENV_IMAGE_NAME), commit_seconds


def build_env_image(project_dir: Path, out_dir: Path, pip_log: Path) -> dict[str, object]:
    """WHAT: Builds the v1-baseline-env image: installs v1's requirements.txt unchanged inside a container,
    then the dev requirements (pytest), then freezes and commits. The build container is always removed.
    WHY: Records install time and package list separately from the later pytest time.

    Args:
        project_dir: sentinel_flow_mvp folder of a fresh clone (with requirements-dev.txt copied in).
        out_dir: output folder.
        pip_log: pip log path.
    Returns:
        Facts for meta.json (image ID, freeze hash, pip seconds, build seconds, cache state).
    Raises:
        RuntimeError: if the v1 install, freeze or commit fails (the run must stop and be reported).
    Example:
        success -> {"env_image_id": "sha256:...", "env_pip_v1_wall_seconds": 3000.1, ...}
    """
    cache_before = cache_state()
    started = time.monotonic()
    start_env_container(project_dir)
    try:
        v1_install = exec_pip("/tmp/requirements.txt", pip_log)
        if not v1_install.succeeded:
            raise RuntimeError(f"pip install of v1 requirements failed (rc={v1_install.return_code}); see pip.log")
        dev_install = exec_pip("/tmp/requirements-dev.txt", pip_log)
        freeze_hash, env_image_id, save_seconds = freeze_and_commit(out_dir)
    finally:
        stop_container(ENV_CONTAINER_NAME)
    return {"env_image_name": ENV_IMAGE_NAME, "env_image_id": env_image_id, "pip_freeze_sha256": freeze_hash,
            "cache_before_run": cache_before, "install_cache_label": cache_label(cache_before),
            "env_pip_v1_exit_code": v1_install.return_code, "env_pip_v1_wall_seconds": round(v1_install.wall_seconds, 1),
            "env_pip_dev_wall_seconds": round(dev_install.wall_seconds, 1),
            "env_image_save_wall_seconds": round(save_seconds, 1),
            "env_build_total_wall_seconds": round(time.monotonic() - started, 1)}


def verify_saved_image(earlier_dir: Path, out_dir: Path) -> dict[str, object]:
    """WHAT: Checks that the saved image v1-baseline-env is exactly the one an earlier run recorded:
    same image ID and same pip freeze SHA-256. Writes the fresh freeze to out_dir/pip-freeze.txt.
    WHY: The owner allowed reusing the image only if it is provably the same one.

    Args:
        earlier_dir: output folder of the run that built the image (holds meta.json).
        out_dir: output folder of the current run.
    Returns:
        The earlier run's build facts plus "env_image_reused_from" and "env_image_verified".
    Raises:
        RuntimeError: if the image ID or the freeze hash differs.
    Example:
        same ID and same freeze hash -> facts with env_image_verified True
    """
    loaded = json.loads((earlier_dir / "meta.json").read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"expected JSON object in {earlier_dir / 'meta.json'}")
    earlier: dict[str, object] = loaded
    current_id = image_id(ENV_IMAGE_NAME)
    arguments = ["docker", "run", "--rm", ENV_IMAGE_NAME, "python", "-m", "pip", "freeze"]
    freeze = base.run_command(arguments, None, base.SHORT_COMMAND_TIMEOUT_SECONDS)
    (out_dir / "pip-freeze.txt").write_text(freeze.stdout, encoding="utf-8")
    freeze_hash = base.sha256_of_file(out_dir / "pip-freeze.txt")
    if current_id != earlier["env_image_id"] or freeze_hash != earlier["pip_freeze_sha256"]:
        raise RuntimeError("saved image differs from the recorded one (ID or freeze hash); not reusing it")
    earlier.pop("package_diff", None)
    earlier.update({"env_image_reused_from": str(earlier_dir), "env_image_verified": True})
    return earlier


def compare_with_earlier_run(out_dir: Path) -> dict[str, object]:
    """WHAT: Compares the new image's pip freeze with the packages the no-services run installed,
    saves the full difference to package-diff.json, and returns it.
    WHY: If the two environments differ, the with-db results are not directly comparable.

    Args:
        out_dir: output folder holding pip-freeze.txt (and receiving package-diff.json).
    Returns:
        The difference dict, or {"status": "earlier pip log not found"}.
    Example:
        identical package sets -> only_in_image [], only_in_earlier_run [], version_differences []
    """
    earlier_log = out_dir.parent / NO_SERVICES_PIP_LOG
    if not earlier_log.exists():
        return {"status": "earlier pip log not found"}
    image_packages = parse_freeze((out_dir / "pip-freeze.txt").read_text(encoding="utf-8"))
    earlier_packages = parse_installed_from_log(earlier_log.read_text(encoding="utf-8", errors="replace"))
    difference = compare_packages(image_packages, earlier_packages)
    (out_dir / "package-diff.json").write_text(json.dumps(difference, indent=2), encoding="utf-8")
    return difference


def update_meta_json(out_dir: Path, extra: dict[str, object]) -> None:
    """WHAT: Adds keys to the existing meta.json.
    WHY: pytest time is only known after pytest ends, but meta.json is first written before it.

    Args:
        out_dir: output folder.
        extra: keys to add or replace.
    Returns:
        Nothing.
    Example:
        {"pytest_wall_seconds": 240.1} -> meta.json now has that key
    """
    meta_path = out_dir / "meta.json"
    fields = json.loads(meta_path.read_text(encoding="utf-8"))
    fields.update(extra)
    write_meta_json(out_dir, fields)


def execute_env_run(args: argparse.Namespace, work_root: Path, out_dir: Path) -> int:
    """WHAT: The with-db flow: fresh clone, build the v1-baseline-env image, compare packages,
    then run pytest (same arguments) from that image with services started.
    WHY: Separates "where the environment lives" from "how it is installed" so timing can be compared.

    Args:
        args: parsed command line.
        work_root: new temp folder.
        out_dir: output folder.
    Returns:
        0 on completion.
    Raises:
        RuntimeError: if Docker is unavailable or the image build fails.
    Example:
        --run-label docker-with-db -> meta.json with env_pip_v1_wall_seconds and pytest_wall_seconds
    """
    if not base.docker_available():
        raise RuntimeError("Docker is not available")
    build_image()
    project_dir = base.clone_v1(args.tag, work_root / "v1")
    commit = base.read_commit_hash(work_root / "v1")
    shutil.copy(Path(__file__).resolve().parents[2] / "requirements-dev.txt", project_dir / "requirements-dev.txt")
    pip_log = out_dir / "pip.log"
    pip_log.unlink(missing_ok=True)
    if args.reuse_env_from:
        facts = verify_saved_image(Path(args.reuse_env_from), out_dir)
    else:
        facts = build_env_image(project_dir, out_dir, pip_log)
    facts["package_diff"] = compare_with_earlier_run(out_dir)
    write_meta_json(out_dir, facts)
    args.image_name = ENV_IMAGE_NAME
    result = run_inside_container(args, project_dir, out_dir, "python", ENV_IMAGE_NAME, True)
    update_meta_json(out_dir, {"pytest_wall_seconds": round(result.wall_seconds, 1)})
    note = "Environment installed into the container filesystem (image v1-baseline-env), warm wheel cache."
    record_container_outcome(args, commit, project_dir, out_dir, result, note)
    for log_name in ["pip.log", "pytest.log", "pytest-stderr.log"]:
        base.trim_log_if_large(out_dir / log_name)
    return 0


def mask_url(url: str) -> str:
    """WHAT: Replaces the password part of a URL like scheme://user:password@host with ****.
    WHY: Credentials must never be printed or saved in results.

    Args:
        url: connection URL.
    Returns:
        The URL with the password masked.
    Example:
        "postgresql://postgres:secret@timescaledb:5432/db" -> "postgresql://postgres:****@timescaledb:5432/db"
    """
    return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1****@", url)


def read_service_env(project_dir: Path) -> dict[str, str]:
    """WHAT: Reads DATABASE_URL and REDIS_URL from v1's own .env.example (the project's example config).
    WHY: Inside the compose network the services are reached by name (timescaledb, redis), not localhost.

    Args:
        project_dir: sentinel_flow_mvp folder.
    Returns:
        {"DATABASE_URL": ..., "REDIS_URL": ...}.
    Raises:
        RuntimeError: if either key is missing.
    Example:
        a line "REDIS_URL=redis://redis:6379/0" -> {"REDIS_URL": "redis://redis:6379/0", ...}
    """
    wanted = ["DATABASE_URL", "REDIS_URL"]
    found: dict[str, str] = {}
    for line in (project_dir / ".env.example").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in wanted:
            found[key.strip()] = value.strip()
    if sorted(found) != sorted(wanted):
        raise RuntimeError(f".env.example lacks one of {wanted}")
    return found


def prove_connectivity(
    project_dir: Path, out_dir: Path, network: str, image: str, environment: dict[str, str]
) -> None:
    """WHAT: From a container shaped like the test container (same image, network and variables), checks
    Postgres with pg_isready plus a real SELECT 1, and Redis with PING. Output goes to connectivity-check.txt
    (passwords masked).
    WHY: A run may only be labelled "with-db" if the database was really reachable.

    Args:
        project_dir: sentinel_flow_mvp folder.
        out_dir: output folder.
        network: compose network.
        image: image to run.
        environment: variables for the container.
    Returns:
        Nothing.
    Raises:
        RuntimeError: if any check fails.
    Example:
        services up -> file lists "accepting connections", "select 1 -> 1", "redis ping -> True"
    """
    code = "; ".join(["import os, psycopg2, redis",
                      "c = psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=10)",
                      "k = c.cursor()", "k.execute('select 1')", "print('postgres select 1 ->', k.fetchone()[0])",
                      "r = redis.Redis.from_url(os.environ['REDIS_URL'], socket_connect_timeout=10)",
                      "print('redis ping ->', r.ping())"])
    report = [f"DATABASE_URL={mask_url(environment['DATABASE_URL'])}", f"REDIS_URL={mask_url(environment['REDIS_URL'])}"]
    commands = [["pg_isready", "-h", "timescaledb", "-p", "5432"], ["python", "-c", code]]
    failed = False
    for command in commands:
        arguments = container_arguments(project_dir, out_dir, network, command, "medora-v1-check", False, image, environment)
        finished = base.run_command(arguments, None, base.SHORT_COMMAND_TIMEOUT_SECONDS)
        report.append(f"$ {command[0]} -> rc={finished.returncode} {finished.stdout.strip()} {finished.stderr.strip()[-300:]}")
        failed = failed or finished.returncode != 0
    (out_dir / "connectivity-check.txt").write_text(chr(10).join(report) + chr(10), encoding="utf-8")
    if failed:
        raise RuntimeError("database/redis not reachable from the test container; see connectivity-check.txt")


def run_inside_container(
    args: argparse.Namespace, project_dir: Path, out_dir: Path,
    python_command: str = "/work/.venv/bin/python", image: str = IMAGE_NAME,
    use_services_env: bool = False,
) -> base.RunResult:
    """WHAT: Runs pytest in the container; for docker-with-db it first starts v1's timescaledb and redis
    and joins the container to their compose network, and always stops them afterwards (no -v).
    WHY: v1's settings reach Redis by the hostname "redis", which only works on that network;
    the network name is read from Docker, not assumed.

    Args:
        args: parsed command line.
        project_dir: sentinel_flow_mvp folder.
        out_dir: output folder.
        python_command: python inside the container.
        image: image to run.
        use_services_env: pass v1's DATABASE_URL/REDIS_URL and prove the connections first.
    Returns:
        RunResult.
    Raises:
        RuntimeError: if Docker or compose fails, or the connectivity proof fails.
    Example:
        "docker-with-db" -> compose up timescaledb+redis, pytest on network, compose down
    """
    if args.run_label == "docker-no-services":
        return container_run_pytest(project_dir, out_dir, args.run_label, None, python_command, image, None, args.test_path)
    compose_file = project_dir / "docker-compose.yml"
    try:
        override_file = project_dir / "docker-compose.no-ports.yml"
        override_file.write_text(PORT_OVERRIDE_TEXT, encoding="utf-8")
        compose_with_override(compose_file, override_file, ["up", "-d", "--wait"] + base.DB_SERVICE_NAMES)
        network = discover_compose_network(compose_file, out_dir)
        environment = read_service_env(project_dir) if use_services_env else None
        if environment is not None:
            prove_connectivity(project_dir, out_dir, network, image, environment)
        return container_run_pytest(project_dir, out_dir, args.run_label, network, python_command, image, environment, args.test_path)
    finally:
        compose_with_override(compose_file, project_dir / "docker-compose.no-ports.yml", ["down"])


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
    parser.add_argument("--reuse-env-from", default=None, help="out folder of an earlier run; reuse its saved image if provably identical")
    parser.add_argument("--test-path", default="tests", help="path pytest collects, relative to sentinel_flow_mvp")
    parser.add_argument("--fresh-cache", action="store_true", help="do not mount the wheel-cache volume (cold install)")
    parser.add_argument("--tag", default=base.V1_DEFAULT_TAG)
    parser.add_argument("--out-root", default="docs/phase0/v1-test-results")
    args = parser.parse_args(argv)
    out_dir = Path(args.out_root) / args.run_label
    out_dir.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix="v1_suite_docker_"))
    try:
        use_saved_image = args.run_label.startswith("docker-with-db") or bool(args.reuse_env_from)
        executor = execute_env_run if use_saved_image else execute_container_run
        return executor(args, work_root, out_dir)
    finally:
        remove_clone_in_container(work_root)
        shutil.rmtree(work_root, onerror=base.make_writable_and_retry)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
