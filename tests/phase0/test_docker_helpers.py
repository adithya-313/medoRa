# TERM: mask - replace a secret (here a password inside a URL) with **** so it is never printed or saved.
"""Unit tests for small helpers of the container runner. No Docker, no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.phase0.run_v1_suite_docker import mask_url, pytest_command, read_service_env


def test_mask_url_hides_the_password_only() -> None:
    """WHAT: Checks the password in a connection URL is replaced by **** and the rest is kept.
    WHY: Results and logs must show which host was used without exposing credentials.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        "postgresql://postgres:secret@timescaledb:5432/db" -> "postgresql://postgres:****@timescaledb:5432/db"
    """
    masked = mask_url("postgresql://postgres:secret@timescaledb:5432/db")
    assert masked == "postgresql://postgres:****@timescaledb:5432/db"
    assert mask_url("redis://redis:6379/0") == "redis://redis:6379/0"


def test_read_service_env_reads_both_urls(tmp_path: Path) -> None:
    """WHAT: Checks DATABASE_URL and REDIS_URL are read from an .env.example file and other keys ignored.
    WHY: The test container must get exactly the two variables v1's settings read.

    Args:
        tmp_path: pytest's temporary folder.
    Returns:
        Nothing.
    Example:
        file with A=1, DATABASE_URL=x, REDIS_URL=y -> {"DATABASE_URL": "x", "REDIS_URL": "y"}
    """
    (tmp_path / ".env.example").write_text("A=1\nDATABASE_URL=x\nREDIS_URL=y\n", encoding="utf-8")
    assert read_service_env(tmp_path) == {"DATABASE_URL": "x", "REDIS_URL": "y"}


def test_read_service_env_raises_when_a_key_is_missing(tmp_path: Path) -> None:
    """WHAT: Checks a missing REDIS_URL raises RuntimeError.
    WHY: Silently running without a variable would repeat the "database never reached" mistake.

    Args:
        tmp_path: pytest's temporary folder.
    Returns:
        Nothing.
    Example:
        file with only DATABASE_URL -> RuntimeError
    """
    (tmp_path / ".env.example").write_text("DATABASE_URL=x\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        read_service_env(tmp_path)


def test_pytest_command_has_no_filters_and_uses_the_given_path() -> None:
    """WHAT: Checks the pytest command keeps the cache off, writes junit XML, adds no -k/-x filter,
    and ends with exactly the path it was given.
    WHY: Runs on different test folders must use identical arguments except the path.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        ("python", "app/health_risk/tests") -> [..., "--ignore=tests/load", "app/health_risk/tests"]
    """
    command = pytest_command("python", "app/health_risk/tests")
    assert command[-1] == "app/health_risk/tests"
    assert "-p" in command and "no:cacheprovider" in command
    assert "--junitxml=/out/junit.xml" in command
    assert "-k" not in command and "-x" not in command
