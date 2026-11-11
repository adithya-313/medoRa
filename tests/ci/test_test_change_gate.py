"""Unit tests for the CI test change gate."""

from __future__ import annotations

import io

import pytest

from tools.ci.test_change_gate import decide, main, parse_name_status

pytestmark = pytest.mark.unit


def test_parse_name_status_empty_diff() -> None:
    """Empty diff returns an empty list of changes."""
    assert parse_name_status("") == []
    assert parse_name_status("   \n\n  ") == []


def test_parse_name_status_standard_lines() -> None:
    """Standard status lines with two fields are parsed correctly."""
    diff_text = "M\ttests/test_foo.py\nA\tcloud/app.py\nD\ttests/test_bar.py\n"
    expected = [
        ("M", "tests/test_foo.py"),
        ("A", "cloud/app.py"),
        ("D", "tests/test_bar.py"),
    ]
    assert parse_name_status(diff_text) == expected


def test_parse_name_status_rename_retains_old_path() -> None:
    """Rename lines with three fields retain the old path."""
    diff_text = "R100\ttests/old_test.py\ttests/new_test.py\n"
    assert parse_name_status(diff_text) == [("R100", "tests/old_test.py")]


def test_parse_name_status_malformed_line_raises_value_error() -> None:
    """Malformed diff lines without proper tab separation raise ValueError."""
    with pytest.raises(ValueError, match="Malformed"):
        parse_name_status("invalid line without tabs")

    with pytest.raises(ValueError, match="Malformed"):
        parse_name_status("M\tpath1\textra_field")

    with pytest.raises(ValueError, match="Malformed rename"):
        parse_name_status("R100\tonly_one_path")


def test_decide_modified_under_tests() -> None:
    """Modification of an existing test requires review."""
    changes = [("M", "tests/support/test_markers.py")]
    assert decide(changes) == ["tests/support/test_markers.py"]


def test_decide_deleted_under_tests() -> None:
    """Deletion of an existing test requires review."""
    changes = [("D", "tests/unit/test_legacy.py")]
    assert decide(changes) == ["tests/unit/test_legacy.py"]


def test_decide_renamed_under_tests() -> None:
    """Renaming a test file requires review."""
    changes = [("R100", "tests/unit/test_old.py")]
    assert decide(changes) == ["tests/unit/test_old.py"]


def test_decide_added_under_tests() -> None:
    """Adding a new test does not require review."""
    changes = [("A", "tests/unit/test_new_feature.py")]
    assert decide(changes) == []


def test_decide_code_only_change() -> None:
    """Changes to application code outside tests and CI do not require review."""
    changes = [
        ("M", "cloud/main.py"),
        ("A", "desktop/counter.ts"),
        ("D", "owner/pwa.ts"),
    ]
    assert decide(changes) == []


def test_decide_workflow_change() -> None:
    """Any change under .github/workflows/ requires review."""
    changes = [("A", ".github/workflows/ci.yml")]
    assert decide(changes) == [".github/workflows/ci.yml"]


def test_decide_gate_script_change() -> None:
    """Any change to tools/ci/test_change_gate.py requires review."""
    changes = [("M", "tools/ci/test_change_gate.py")]
    assert decide(changes) == ["tools/ci/test_change_gate.py"]


def test_decide_rename_out_of_tests() -> None:
    """Renaming a test file out of tests/ keeps old path and triggers review."""
    diff_text = "R100\ttests/unit/test_moved.py\tmisc/test_moved.py\n"
    parsed = parse_name_status(diff_text)
    assert parsed == [("R100", "tests/unit/test_moved.py")]
    assert decide(parsed) == ["tests/unit/test_moved.py"]


def test_main_exit_0_on_clean_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() exits 0 when no changes require review."""
    clean_diff = "A\ttests/test_brand_new.py\nM\tcloud/app.py\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(clean_diff))
    assert main() == 0


def test_main_exit_3_on_reviewed_diff(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() prints required paths and exits 3 when changes need review."""
    review_diff = "M\ttests/test_existing.py\nM\t.github/workflows/ci.yml\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(review_diff))
    assert main() == 3
    captured = capsys.readouterr()
    assert ".github/workflows/ci.yml\ntests/test_existing.py" in captured.out
