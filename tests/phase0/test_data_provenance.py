# TERM: provenance - where data came from, who may use it, and under what licence.
# TERM: fixture - a tiny file or folder built inside pytest's temp folder for one test.
# TERM: coverage - every discovered file and host has exactly one entry in provenance.yaml.
# TERM: sha256 - a 64-character fingerprint of a file's bytes.
"""Unit tests for tools/phase0/data_provenance.py. Tiny fixtures; local git only; no network."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.phase0.data_provenance import (
    UNKNOWN_LICENCE,
    Entry,
    GitEvidence,
    Licence,
    ProvenanceError,
    check_coverage,
    check_git_evidence,
    clone_arguments,
    count_csv,
    count_jsonl,
    discover_data_files,
    discover_external_urls,
    measure_file,
    parse_entry,
)

pytestmark = pytest.mark.unit

GOOD_LICENCE = {
    "value": "public domain",
    "evidence_url": "https://example.org/terms",
    "retrieved_on": "2026-10-21",
    "quote": "is public domain",
}
CODE_SOURCE = {"type": "code", "file": "a.py", "line": 1, "contains": "x"}


def raw_entry(**changes: object) -> dict[str, object]:
    """WHAT: Builds a valid raw provenance.yaml file entry as a dict, with optional field replacements.
    WHY: Each validation test changes exactly one field of an otherwise valid entry.

    Args:
        **changes: fields to replace or add.
    Returns:
        A dict that parse_entry accepts.
    Example:
        raw_entry(licence={"value": "x"}) -> the valid entry with a different licence block
    """
    entry: dict[str, object] = {
        "path": "data/a.csv",
        "kind": "dataset",
        "source": [CODE_SOURCE],
        "licence": dict(GOOD_LICENCE),
        "personal_data": {"value": "no", "reason": "product columns only"},
        "production_use": {"value": "not-for-production", "reason": "test"},
    }
    entry.update(changes)
    return entry


def make_entry(key: str) -> Entry:
    """WHAT: Builds a minimal Entry object with the given key.
    WHY: Coverage tests need entries but not their details.

    Args:
        key: the entry key (a path or host).
    Returns:
        An Entry.
    Example:
        make_entry("data/a.csv") -> Entry(key="data/a.csv", kind="dataset", ...)
    """
    return Entry(
        key, "dataset", [], Licence(UNKNOWN_LICENCE, "", "", ""), "no", "r", "not-for-production", "r"
    )


def test_csv_quoted_line_break_counts_as_one_row(tmp_path: Path) -> None:
    """WHAT: A CSV with a header and two records, one containing a quoted line break, has 2 rows (not 3).
    WHY: Counting text lines would overstate rows for records with embedded newlines.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        4 text lines, 2 records -> (2, ["id", "note"])
    """
    csv_path = tmp_path / "a.csv"
    csv_path.write_text(
        "id,note" + chr(10) + '1,"line one' + chr(10) + 'line two"' + chr(10) + "2,plain" + chr(10),
        encoding="utf-8",
    )
    assert count_csv(csv_path, ",") == (2, ["id", "note"])
    assert measure_file(csv_path).row_count == 2


def test_jsonl_blank_lines_are_ignored(tmp_path: Path) -> None:
    """WHAT: A JSONL file with two records and blank lines between them counts 2.
    WHY: Blank lines are not records.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        lines "{}", "", "   ", "{}" -> 2
    """
    jsonl_path = tmp_path / "a.jsonl"
    jsonl_path.write_text("{}" + chr(10) + chr(10) + "   " + chr(10) + "{}" + chr(10), encoding="utf-8")
    assert count_jsonl(jsonl_path) == 2


def test_sha256_and_size_of_a_known_fixture(tmp_path: Path) -> None:
    """WHAT: The SHA-256 of a file containing exactly "abc" matches the published test vector, and the size is 3.
    WHY: Proves the streamed hashing is correct.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        b"abc" -> ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
    """
    path = tmp_path / "abc.bin"
    path.write_bytes(b"abc")
    measured = measure_file(path)
    assert measured.sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert measured.bytes == 3 and measured.row_count is None


def test_coverage_catches_missing_duplicated_and_unknown_entries() -> None:
    """WHAT: A file with no entry is "uncovered", two entries for one file are "duplicated", and an entry for
    something v1 does not have is "not found in v1".
    WHY: Nothing may be forgotten, counted twice, or invented.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        discovered [a, b], entries [a, a, ghost] -> uncovered b; duplicated a; ghost not found
    """
    entries = [make_entry("a.csv"), make_entry("a.csv"), make_entry("ghost.csv")]
    problems = check_coverage(["a.csv", "b.csv"], entries)
    text = chr(10).join(problems)
    assert "uncovered: b.csv" in text and "duplicated (2 entries): a.csv" in text
    assert "entry not found in v1: ghost.csv" in text
    assert check_coverage(["a.csv"], [make_entry("a.csv")]) == []


def test_licence_value_without_evidence_url_is_rejected() -> None:
    """WHAT: A licence with a value but no evidence_url is rejected; the "unknown" value with empty evidence
    is accepted; "unknown" with evidence fields filled in is rejected.
    WHY: A licence is never recorded without fetched evidence.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        {"value": "MIT"} -> ProvenanceError("... needs evidence_url, retrieved_on and quote")
    """
    with pytest.raises(ProvenanceError, match="needs evidence_url, retrieved_on and quote"):
        parse_entry(raw_entry(licence={"value": "MIT"}))
    assert parse_entry(raw_entry(licence={"value": UNKNOWN_LICENCE})).licence.value == UNKNOWN_LICENCE
    with pytest.raises(ProvenanceError, match="must leave evidence_url"):
        parse_entry(raw_entry(licence={"value": UNKNOWN_LICENCE, "quote": "x"}))


def test_allowed_with_unknown_licence_and_bad_kinds_are_rejected() -> None:
    """WHAT: production_use "allowed" with an unknown licence is rejected; a log marked anything but "discard"
    is rejected; an entry with a bad personal_data value is rejected; a good entry parses.
    WHY: The conclusions must follow the evidence.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        unknown licence + allowed -> ProvenanceError("... 'allowed' needs a known licence ...")
    """
    allowed = {"value": "allowed", "reason": "r"}
    with pytest.raises(ProvenanceError, match="'allowed' needs a known licence"):
        parse_entry(raw_entry(licence={"value": UNKNOWN_LICENCE}, production_use=allowed))
    with pytest.raises(ProvenanceError, match="must be 'discard'"):
        parse_entry(raw_entry(kind="log", production_use={"value": "not-for-production", "reason": "r"}))
    with pytest.raises(ProvenanceError, match="personal_data needs a value"):
        parse_entry(raw_entry(personal_data={"value": "maybe", "reason": "r"}))
    assert parse_entry(raw_entry()).key == "data/a.csv"


def test_external_entry_rules() -> None:
    """WHAT: An external-API entry needs auth_needed in yes/no/unknown, a terms_url when its licence is known,
    and no terms_url while the licence is unknown.
    WHY: Terms links and fetched terms must go together.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        a host entry with a known licence and no terms_url -> ProvenanceError("... fetched terms need a terms_url")
    """
    host = {"url": "https://api.example.org", "kind": "external-api", "auth_needed": "no"}
    base = raw_entry(**host)
    base.pop("path")
    with pytest.raises(ProvenanceError, match="fetched terms need a terms_url"):
        parse_entry(base)
    with pytest.raises(ProvenanceError, match="auth_needed must be one of"):
        parse_entry({**base, "terms_url": "https://example.org/terms", "auth_needed": "maybe"})
    assert parse_entry({**base, "terms_url": "https://example.org/terms"}).key == "api.example.org"


def test_discovery_filters_files_and_groups_urls(tmp_path: Path) -> None:
    """WHAT: In a tiny git repo, discovery lists tracked data files only (not node_modules, package.json or
    other extensions), and finds URLs grouped by host while skipping localhost and example domains, plus
    pytrends from requirements.txt.
    WHY: The inventory must be exact; hidden-URL libraries must still be flagged.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        tracked data/a.csv, frontend/node_modules/x.json, package.json -> ["data/a.csv"]
    """
    files = {
        "data/a.csv": "x",
        "frontend/node_modules/x.json": "{}",
        "frontend/package.json": "{}",
        "notes.txt": "x",
        "app/main.py": 'U = "https://api.fda.gov/drug"  # http://localhost:8080 https://example.com/a',
        "scripts/s.py": "# https://api.fda.gov/other",
        "requirements.txt": "pytrends==4.9.2" + chr(10),
    }
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, timeout=60)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, timeout=60)
    assert discover_data_files(tmp_path) == ["data/a.csv"]
    hits = discover_external_urls(tmp_path)
    assert sorted(hits) == ["api.fda.gov", "library:pytrends"] and len(hits["api.fda.gov"]) == 2


def test_git_evidence_must_name_the_first_adding_commit(tmp_path: Path) -> None:
    """WHAT: In a tiny repo, a git source naming the commit that first added a file (with its exact subject)
    passes; a wrong subject or wrong commit is reported.
    WHY: The "when did this file enter the repo" claim must come from git.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        commit abc1234 "add data" first added data/a.csv -> GitEvidence("abc1234", "add data") passes
    """
    data_file = tmp_path / "sentinel_flow_mvp" / "data" / "a.csv"
    data_file.parent.mkdir(parents=True)
    data_file.write_text("id" + chr(10), encoding="utf-8")
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.org"]
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, timeout=60)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, timeout=60)
    subprocess.run(
        ["git"] + identity + ["commit", "-q", "-m", "add data"], cwd=tmp_path, check=True, timeout=60
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True, timeout=60
    ).stdout.strip()
    assert check_git_evidence(GitEvidence(head[:7], "add data"), "data/a.csv", tmp_path) is None
    assert "not" in (check_git_evidence(GitEvidence(head[:7], "other subject"), "data/a.csv", tmp_path) or "")
    assert "no commit" in (
        check_git_evidence(GitEvidence(head[:7], "add data"), "data/missing.csv", tmp_path) or ""
    )


def test_clone_arguments_disable_line_ending_conversion() -> None:
    """WHAT: Checks the clone command turns core.autocrlf off, uses --depth 1 only for shallow clones, and
    passes the branch.
    WHY: On Windows, CRLF conversion would change sizes and SHA-256 values, so the audit would differ per machine.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        shallow v1-final -> [..., "-c", "core.autocrlf=false", "clone", "--depth", "1", "--branch", "v1-final", ...]
    """
    shallow = clone_arguments("v1-final", Path("t"), True)
    full = clone_arguments("v1-legacy", Path("t"), False)
    assert (
        "core.autocrlf=false" in shallow
        and "--depth" in shallow
        and shallow[shallow.index("--branch") + 1] == "v1-final"
    )
    assert "core.autocrlf=false" in full and "--depth" not in full
