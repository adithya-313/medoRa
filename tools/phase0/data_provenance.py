# TERM: provenance - where a piece of data came from, who may use it, and under what licence.
# TERM: licence - the written permission (or lack of it) that says how data may be used; never guessed here.
# TERM: sha256 - a 64-character fingerprint of a file's bytes; any change to the file changes it.
# TERM: tracked file - a file that git stores (listed by `git ls-files`), as opposed to an untracked local file.
# TERM: external API - a web service the code calls (a host such as api.fda.gov, or a library that hides one).
# TERM: personal data - information that identifies a person (names, phone numbers, ...).
"""Audit of the data v1 ships and the outside services it calls (Phase 0 data provenance).

provenance.yaml holds what people know (source, licence, personal data, production use) as evidence items.
This tool computes everything measurable (size, SHA-256, rows, columns) and checks that every discovered
file and host has exactly one entry and that every citation still holds. Standard library plus PyYAML.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

# The script is run as `python tools/phase0/data_provenance.py`, so the repo root must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.phase0.feature_truth import Evidence, FeatureTruthError, check_one_evidence, parse_evidence  # noqa: E402
from tools.phase0.run_v1_suite import V1_DEFAULT_TAG, V1_REPO_URL, make_writable_and_retry, run_command  # noqa: E402

DATA_EXTENSIONS = [".csv", ".tsv", ".json", ".jsonl", ".parquet", ".xlsx", ".sql", ".db", ".sqlite", ".pkl",
                   ".pt", ".bin", ".safetensors", ".npy", ".log", ".png", ".jpg"]
# Listed in the report so a reader knows what was deliberately left out of the file inventory.
EXCLUSIONS = ["any path containing node_modules", "package.json and package-lock.json (any folder)",
              "tsconfig.json (any folder)"]
IGNORED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]
# sourced: client libraries in requirements.txt that call an outside service without a URL in the code.
HIDDEN_URL_LIBRARIES = {"pytrends": "Google Trends (unofficial client, not a Google API)"}
KINDS = ["dataset", "derived-dataset", "test-fixture", "generated-output", "schema", "log", "external-api"]
FILE_KINDS = [kind for kind in KINDS if kind != "external-api"]
PRODUCTION_USES = ["allowed", "test-fixture-only", "not-for-production", "discard"]
PERSONAL_DATA_VALUES = ["yes", "no", "unknown"]
AUTH_VALUES = ["yes", "no", "unknown"]
UNKNOWN_LICENCE = "unknown — verify manually"
# policy: stream big files in 1 MiB pieces so memory stays constant even for the 70 MB CSV.
CHUNK_BYTES = 1024 * 1024
# policy: a full-history clone of v1-legacy (382 commits plus a 70 MB CSV) is generous at ten minutes.
GIT_TIMEOUT_SECONDS = 600
URL_PATTERN = re.compile(r"https?://[^\s'\"<>)\]}`,]+")


class ProvenanceError(ValueError):
    """Raised when a provenance.yaml entry is malformed; the message names the path, host or library."""


@dataclasses.dataclass(frozen=True)
class GitEvidence:
    """A claim about the commit that first added a file: its hash (or prefix) and exact subject line."""

    commit: str
    message: str


@dataclasses.dataclass
class Licence:
    """A licence or terms statement. When value is UNKNOWN_LICENCE the evidence fields must be empty."""

    value: str
    evidence_url: str
    retrieved_on: str
    quote: str


@dataclasses.dataclass
class Entry:
    """One audited item: a file, an external host or a hidden-URL library."""

    key: str
    kind: str
    source: list[Evidence | GitEvidence]
    licence: Licence
    personal_data: str
    personal_data_reason: str
    production_use: str
    production_reason: str
    terms_url: str = ""
    auth_needed: str = ""
    note: str = ""


@dataclasses.dataclass
class UrlHit:
    """One place in the code where a URL appears."""

    file: str
    line: int
    url: str


@dataclasses.dataclass
class Measurement:
    """Facts computed from a file's bytes: never typed by hand."""

    bytes: int
    sha256: str
    row_count: int | None
    columns: list[str]
    json_type: str


def sha256_and_size(path: Path) -> tuple[str, int]:
    """WHAT: Computes a file's SHA-256 and byte size by reading it in 1 MiB chunks.
    WHY: The 70 MB CSV must never be loaded into memory at once; memory stays constant.

    Args:
        path: file to read.
    Returns:
        (hex SHA-256, size in bytes).
    Example:
        a file containing b"abc" -> ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", 3)
    """
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        chunk = handle.read(CHUNK_BYTES)
        while chunk:
            digest.update(chunk)
            total += len(chunk)
            chunk = handle.read(CHUNK_BYTES)
    return digest.hexdigest(), total


def count_csv(path: Path, delimiter: str) -> tuple[int, list[str]]:
    """WHAT: Counts the data rows (header excluded) and reads the header with the csv module.
    Quoted line breaks inside a field are handled, so one record is one row however many lines it spans.
    WHY: Counting lines would be wrong for records with embedded newlines.

    Args:
        path: CSV or TSV file.
        delimiter: "," or the tab character.
    Returns:
        (row count, header column names); (0, []) for an empty file.
    Example:
        a header plus 2 records, one with a quoted line break -> (2, [the header names])
    """
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        header = next(reader, [])
        rows = sum(1 for _ in reader)
    return rows, header


def count_jsonl(path: Path) -> int:
    """WHAT: Counts the non-empty lines of a JSON-lines file.
    WHY: Blank lines are not records.

    Args:
        path: JSONL file.
    Returns:
        The number of non-empty lines.
    Example:
        lines "{}", "", "{}" -> 2
    """
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def describe_json(path: Path) -> tuple[str, int]:
    """WHAT: Reads a JSON file and returns its top-level type name and its length.
    WHY: A dict of 3 keys and a list of 3 items are different shapes worth recording.

    Args:
        path: JSON file.
    Returns:
        (type name such as "dict" or "list", number of items or keys; 0 for a scalar).
    Raises:
        json.JSONDecodeError: if the file is not valid JSON.
    Example:
        a file containing [1, 2, 3] -> ("list", 3)
    """
    loaded = json.loads(path.read_text(encoding="utf-8"))
    length = len(loaded) if isinstance(loaded, (dict, list)) else 0
    return type(loaded).__name__, length


def measure_file(path: Path) -> Measurement:
    """WHAT: Measures one file: bytes and SHA-256 for every file; plus rows and columns for csv/tsv,
    non-empty line count for jsonl, and top-level type and length for json.
    WHY: Every number in the report comes from here, so none is typed by hand.

    Args:
        path: file to measure.
    Returns:
        A Measurement (row_count None and columns [] when the format has none).
    Raises:
        OSError: if the file cannot be read.
    Example:
        a 2-record CSV -> Measurement(bytes=..., sha256=..., row_count=2, columns=[...], json_type="")
    """
    sha, size = sha256_and_size(path)
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        rows, header = count_csv(path, "\t" if suffix == ".tsv" else ",")
        return Measurement(size, sha, rows, header, "")
    if suffix == ".jsonl":
        return Measurement(size, sha, count_jsonl(path), [], "")
    if suffix == ".json":
        json_type, length = describe_json(path)
        return Measurement(size, sha, length, [], json_type)
    return Measurement(size, sha, None, [], "")


def git_output(arguments: list[str], working_dir: Path) -> str:
    """WHAT: Runs one git command (list arguments, timeout) and returns its output; raises on failure.
    WHY: One place guarantees no shell, a timeout and a return-code check for every git call.

    Args:
        arguments: git arguments after "git".
        working_dir: folder to run in.
    Returns:
        Standard output text.
    Raises:
        RuntimeError: if git returns a non-zero code.
    Example:
        git_output(["rev-parse", "HEAD"], clone) -> "3b121e1e14ac77e8883864136e7409bce7c983aa" plus newline
    """
    finished = run_command(["git"] + arguments, working_dir, GIT_TIMEOUT_SECONDS)
    if finished.returncode != 0:
        raise RuntimeError(f"git {' '.join(arguments)} failed: {finished.stderr.strip()}")
    return finished.stdout


def discover_data_files(v1_dir: Path) -> list[str]:
    """WHAT: Lists every git-tracked file under v1_dir whose extension is in DATA_EXTENSIONS, except the
    EXCLUSIONS (node_modules, package*.json, tsconfig.json).
    WHY: Only tracked files ship with the code; untracked local files are not part of v1.

    Args:
        v1_dir: the sentinel_flow_mvp folder inside a git clone.
    Returns:
        Sorted paths relative to v1_dir, using "/".
    Raises:
        RuntimeError: if git ls-files fails.
    Example:
        tracked data/a.csv, README.md, frontend/package.json -> ["data/a.csv"]
    """
    listing = git_output(["ls-files", "-z"], v1_dir)
    found: list[str] = []
    for relative in listing.split(chr(0)):
        name = relative.rsplit("/", 1)[-1]
        excluded = "node_modules" in relative or name in ("package.json", "package-lock.json", "tsconfig.json")
        if relative and not excluded and Path(relative).suffix.lower() in DATA_EXTENSIONS:
            found.append(relative)
    return sorted(found)


def host_of(url: str) -> str:
    """WHAT: Extracts the host name from a URL (no scheme, port or path).
    WHY: External services are grouped by host.

    Args:
        url: an http or https URL.
    Returns:
        The host, lower-cased.
    Example:
        "https://API.fda.gov:443/drug/label.json?x=1" -> "api.fda.gov"
    """
    without_scheme = re.sub(r"^https?://", "", url)
    return without_scheme.split("/")[0].split(":")[0].lower()


def is_ignored_host(host: str) -> bool:
    """WHAT: True for hosts that are not real external services: localhost, loopback, example domains,
    empty hosts and URL templates (hosts containing braces or a percent sign).
    WHY: They would only add noise to the audit.

    Args:
        host: host name.
    Returns:
        True when the host should be ignored.
    Example:
        "localhost" -> True; "example.com" -> True; "api.fda.gov" -> False
    """
    return host in IGNORED_HOSTS or "example" in host or "{" in host or "%" in host or host == ""


def discover_external_urls(v1_dir: Path) -> dict[str, list[UrlHit]]:
    """WHAT: Finds http/https URLs in the .py files under app/ and scripts/ and groups them by host.
    Ignored hosts are skipped. Libraries in HIDDEN_URL_LIBRARIES that appear in requirements.txt are added
    under the key "library:<name>" (their service URL is not in the code).
    WHY: The audit must list every outside service the code can contact.

    Args:
        v1_dir: the sentinel_flow_mvp folder.
    Returns:
        Map of host (or "library:name") -> places it appears.
    Example:
        one URL https://api.fda.gov/drug/label.json at scripts/a.py line 4 -> {"api.fda.gov": [UrlHit("scripts/a.py", 4, ...)]}
    """
    hosts: dict[str, list[UrlHit]] = {}
    for folder in ("app", "scripts"):
        for path in sorted((v1_dir / folder).rglob("*.py")):
            relative = path.relative_to(v1_dir).as_posix()
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                for match in URL_PATTERN.finditer(line):
                    url = match.group(0).rstrip(".")
                    if not is_ignored_host(host_of(url)):
                        hosts.setdefault(host_of(url), []).append(UrlHit(relative, number, url))
    requirements = (v1_dir / "requirements.txt").read_text(encoding="utf-8").lower()
    for library in HIDDEN_URL_LIBRARIES:
        pinned = re.search(rf"^{library}==\S+", requirements, re.MULTILINE)
        if pinned:
            hosts[f"library:{library}"] = [UrlHit("requirements.txt", 0, pinned.group(0))]
    return hosts


def parse_licence(raw: object, key: str) -> Licence:
    """WHAT: Validates a licence mapping {value, evidence_url, retrieved_on, quote}.
    A real value needs all three evidence fields; UNKNOWN_LICENCE needs them empty.
    WHY: A licence is never written without fetched evidence.

    Args:
        raw: the YAML value.
        key: owning entry key, for error messages.
    Returns:
        Licence.
    Raises:
        ProvenanceError: for a missing value, or evidence that does not match the rule above.
    Example:
        {"value": "unknown — verify manually"} -> Licence(value="unknown — verify manually", "", "", "")
    """
    if not isinstance(raw, dict) or not raw.get("value"):
        raise ProvenanceError(f"{key}: licence needs a value")
    licence = Licence(raw["value"], raw.get("evidence_url", "") or "", raw.get("retrieved_on", "") or "",
                      raw.get("quote", "") or "")
    evidence_fields = [licence.evidence_url, licence.retrieved_on, licence.quote]
    if licence.value == UNKNOWN_LICENCE and any(evidence_fields):
        raise ProvenanceError(f"{key}: an unknown licence must leave evidence_url, retrieved_on and quote empty")
    if licence.value != UNKNOWN_LICENCE and not all(evidence_fields):
        raise ProvenanceError(f"{key}: licence '{licence.value}' needs evidence_url, retrieved_on and quote")
    return licence


def parse_source_item(raw: object, key: str) -> Evidence | GitEvidence:
    """WHAT: Validates one source evidence item: type git (commit, message), code or doc (as in feature_truth).
    WHY: Sources must be checkable by a script.

    Args:
        raw: the YAML value.
        key: owning entry key, for error messages.
    Returns:
        GitEvidence or Evidence.
    Raises:
        ProvenanceError: for a malformed item.
    Example:
        {"type": "git", "commit": "4709bcb", "message": "fix(knowledge_base): ..."} -> GitEvidence(...)
    """
    if isinstance(raw, dict) and raw.get("type") == "git":
        if not raw.get("commit") or not raw.get("message"):
            raise ProvenanceError(f"{key}: a git source needs commit and message")
        return GitEvidence(str(raw["commit"]), str(raw["message"]))
    try:
        return parse_evidence(raw, key)
    except FeatureTruthError as error:
        raise ProvenanceError(str(error)) from error


def parse_entry(raw: object) -> Entry:
    """WHAT: Validates one provenance.yaml entry and builds an Entry. Rules: exactly one of path/url/library;
    kind valid and matching; at least one source item; personal_data and production_use valid;
    production_use "allowed" needs a known licence; generated outputs and logs must be "discard".
    WHY: The audit's conclusions must follow its evidence.

    Args:
        raw: the YAML value.
    Returns:
        Entry.
    Raises:
        ProvenanceError: naming the path, url or library of the bad entry.
    Example:
        production_use allowed with licence "unknown — verify manually" -> ProvenanceError("... needs a known licence")
    """
    if not isinstance(raw, dict):
        raise ProvenanceError(f"entry is not a mapping: {raw!r}")
    keys = [name for name in ("path", "url", "library") if raw.get(name)]
    if len(keys) != 1:
        raise ProvenanceError(f"entry needs exactly one of path, url, library: {raw!r}")
    key = f"library:{raw['library']}" if keys[0] == "library" else host_of(raw["url"]) if keys[0] == "url" else raw["path"]
    entry = build_entry(raw, key, is_file=keys[0] == "path")
    validate_entry_rules(entry, is_file=keys[0] == "path")
    return entry


def build_entry(raw: dict, key: str, is_file: bool) -> Entry:
    """WHAT: Reads the fields of a mapping into an Entry (sources, licence, personal data, production use).
    WHY: Splits field reading from rule checking so each stays short.

    Args:
        raw: the YAML mapping.
        key: entry key.
        is_file: True for file entries.
    Returns:
        Entry (not yet rule-checked).
    Raises:
        ProvenanceError: for missing fields or invalid values.
    Example:
        a complete mapping -> Entry(key="data/a.csv", kind="dataset", ...)
    """
    for field_name in ("kind", "source", "licence", "personal_data", "production_use"):
        if field_name not in raw:
            raise ProvenanceError(f"{key}: missing field '{field_name}'")
    personal = raw["personal_data"]
    production = raw["production_use"]
    if not isinstance(personal, dict) or not isinstance(production, dict):
        raise ProvenanceError(f"{key}: personal_data and production_use must be mappings with value and reason")
    sources = [parse_source_item(item, key) for item in raw["source"] or []]
    return Entry(key, raw["kind"], sources, parse_licence(raw["licence"], key), str(personal.get("value")),
                 str(personal.get("reason", "")), str(production.get("value")), str(production.get("reason", "")),
                 str(raw.get("terms_url", "") or ""), str(raw.get("auth_needed", "") or ""), str(raw.get("note", "") or ""))


def validate_entry_rules(entry: Entry, is_file: bool) -> None:
    """WHAT: Applies the rules that tie kind, licence, personal data and production use together.
    WHY: Prevents "allowed" for data with an unknown licence and keeps reports and logs out of production.

    Args:
        entry: a built Entry.
        is_file: True for file entries.
    Returns:
        Nothing.
    Raises:
        ProvenanceError: naming the entry key.
    Example:
        kind log with production_use allowed -> ProvenanceError("... generated outputs and logs must be discard")
    """
    allowed_kinds = FILE_KINDS if is_file else ["external-api"]
    if entry.kind not in allowed_kinds:
        raise ProvenanceError(f"{entry.key}: kind '{entry.kind}' must be one of {allowed_kinds}")
    if not entry.source:
        raise ProvenanceError(f"{entry.key}: needs at least one source evidence item")
    if entry.personal_data not in PERSONAL_DATA_VALUES or not entry.personal_data_reason:
        raise ProvenanceError(f"{entry.key}: personal_data needs a value in {PERSONAL_DATA_VALUES} and a reason")
    if entry.production_use not in PRODUCTION_USES or not entry.production_reason:
        raise ProvenanceError(f"{entry.key}: production_use needs a value in {PRODUCTION_USES} and a reason")
    if entry.production_use == "allowed" and entry.licence.value == UNKNOWN_LICENCE:
        raise ProvenanceError(f"{entry.key}: 'allowed' needs a known licence, not '{UNKNOWN_LICENCE}'")
    if entry.kind in ("generated-output", "log") and entry.production_use != "discard":
        raise ProvenanceError(f"{entry.key}: generated outputs and logs must be 'discard'")
    if not is_file:
        validate_external_rules(entry)


def validate_external_rules(entry: Entry) -> None:
    """WHAT: Extra rules for external-API entries: auth_needed must be yes, no or unknown; terms_url is set
    exactly when the terms were fetched (a known licence value) and empty when the licence is unknown.
    WHY: A terms link without fetched evidence, or fetched terms without a link, would break the evidence rule.

    Args:
        entry: an external-API Entry.
    Returns:
        Nothing.
    Raises:
        ProvenanceError: naming the host or library.
    Example:
        a known licence with an empty terms_url -> ProvenanceError("... terms_url ...")
    """
    if entry.auth_needed not in AUTH_VALUES:
        raise ProvenanceError(f"{entry.key}: auth_needed must be one of {AUTH_VALUES}")
    known = entry.licence.value != UNKNOWN_LICENCE
    if known and not entry.terms_url:
        raise ProvenanceError(f"{entry.key}: fetched terms need a terms_url")
    if not known and entry.terms_url:
        raise ProvenanceError(f"{entry.key}: terms_url must stay empty while the licence is unknown")


def load_entries(path: Path) -> list[Entry]:
    """WHAT: Reads provenance.yaml (mapping with an "items" list) and validates every entry.
    WHY: Bad input stops here with the offending path, host or library in the message.

    Args:
        path: the YAML file.
    Returns:
        The entries.
    Raises:
        ProvenanceError: for a malformed file or entry.
    Example:
        a file with 2 valid items -> 2 Entry objects
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise ProvenanceError(f"{path}: expected a mapping with an 'items' list")
    return [parse_entry(raw) for raw in document["items"]]


def check_coverage(discovered_keys: list[str], entries: list[Entry]) -> list[str]:
    """WHAT: Checks every discovered file and host has exactly one entry, and every entry was discovered.
    WHY: Nothing may be forgotten or counted twice.

    Args:
        discovered_keys: file paths, hosts and "library:name" keys found in v1.
        entries: validated entries.
    Returns:
        A list of problems (empty when covered exactly once).
    Example:
        a discovered file with no entry -> ["uncovered: data/a.csv"]
    """
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.key] = counts.get(entry.key, 0) + 1
    problems: list[str] = []
    for key in sorted(set(discovered_keys)):
        if counts.get(key, 0) == 0:
            problems.append(f"uncovered: {key}")
    for key, count in sorted(counts.items()):
        if count > 1:
            problems.append(f"duplicated ({count} entries): {key}")
        if key not in discovered_keys:
            problems.append(f"entry not found in v1: {key}")
    return problems


def check_git_evidence(item: GitEvidence, path: str, history_dir: Path) -> str | None:
    """WHAT: Verifies that `item` names the commit that first ADDED `path` on the history clone's branch:
    the oldest `git log --diff-filter=A --follow` entry must start with item.commit and have the exact subject.
    WHY: Proves when a file entered the repository, from git itself.

    Args:
        item: the claimed commit and subject.
        path: file path relative to sentinel_flow_mvp.
        history_dir: full-history clone of v1-legacy (repo root).
    Returns:
        A problem text, or None when the claim holds.
    Example:
        item.commit "4709bcb" and the real first-add commit 4709bcb... with the same subject -> None
    """
    listing = git_output(["log", "--diff-filter=A", "--follow", "--format=%H%x09%s", "--",
                          f"sentinel_flow_mvp/{path}"], history_dir)
    lines = [line for line in listing.splitlines() if line]
    if not lines:
        return f"{path}: git found no commit that added this file"
    first_hash, _, first_subject = lines[-1].partition(chr(9))
    if not first_hash.startswith(item.commit) or first_subject != item.message:
        return f"{path}: first added by {first_hash[:7]} '{first_subject}', not {item.commit} '{item.message}'"
    return None


def check_evidence(entries: list[Entry], v1_dir: Path, history_dir: Path) -> list[str]:
    """WHAT: Checks every source item of every entry: code/doc items through feature_truth's checker,
    git items against the history clone. Returns ALL problems, not only the first.
    WHY: A reviewer should fix every bad citation in one pass.

    Args:
        entries: validated entries.
        v1_dir: the sentinel_flow_mvp folder of the v1-final clone.
        history_dir: full-history clone of v1-legacy.
    Returns:
        A list of problems.
    Example:
        one code citation on the wrong line -> ["<key>: app/x.py:9 does not contain ..."]
    """
    problems: list[str] = []
    for entry in entries:
        for item in entry.source:
            if isinstance(item, GitEvidence):
                problem = check_git_evidence(item, entry.key, history_dir)
            else:
                problem = check_one_evidence(entry.key, item, v1_dir, Path("."), {})
            if problem is not None:
                problems.append(problem)
    return problems


def render_provenance(entries: list[Entry], measurements: dict[str, Measurement], hits: dict[str, list[UrlHit]]) -> str:
    """WHAT: Renders provenance.md: a summary by production_use, the exclusions, one table per kind
    (file facts from the measurements, never from the YAML), and a "Needs manual verification" list.
    WHY: This is the human-readable audit; it is generated, never edited by hand.

    Args:
        entries: validated entries.
        measurements: measured facts per file path.
        hits: URL occurrences per host or library.
    Returns:
        Markdown text.
    Example:
        2 entries (one not-for-production, one discard) -> a summary with those counts and two table rows
    """
    lines = ["# v1 data provenance", "", "_Generated by tools/phase0/data_provenance.py. Do not edit by hand._", "",
             "## Summary by production use", ""]
    for use in PRODUCTION_USES:
        lines.append(f"- {use}: {sum(1 for entry in entries if entry.production_use == use)}")
    lines += ["", "Left out of the file inventory: " + "; ".join(EXCLUSIONS) + "."]
    for kind in KINDS:
        of_kind = [entry for entry in entries if entry.kind == kind]
        if of_kind:
            lines += ["", f"## {kind}", "", "| item | facts (computed) | licence | personal data | production use |", "|---|---|---|---|---|"]
            lines += [render_row(entry, measurements, hits) for entry in of_kind]
    lines += ["", "## Needs manual verification", ""]
    lines += [f"- {entry.key}: licence {entry.licence.value}" for entry in entries if entry.licence.value == UNKNOWN_LICENCE]
    lines += [f"- {entry.key}: personal data unknown" for entry in entries if entry.personal_data == "unknown"]
    return chr(10).join(lines) + chr(10)


def render_row(entry: Entry, measurements: dict[str, Measurement], hits: dict[str, list[UrlHit]]) -> str:
    """WHAT: Renders one table row; the facts column shows measured bytes, rows, columns and SHA-256
    (first 16 characters) for files, or the number of places in the code for hosts and libraries.
    WHY: Facts must come from the measurements, so the YAML cannot disagree with the files.

    Args:
        entry: the entry.
        measurements: measured facts per file path.
        hits: URL occurrences per host or library.
    Returns:
        One Markdown table row.
    Example:
        a 1,705-byte CSV with 9 rows -> "| data/x.csv | 1705 bytes, 9 rows, 7 columns, sha256 ab12... | ..."
    """
    if entry.key in measurements:
        fact = measurements[entry.key]
        rows = f", {fact.row_count} rows" if fact.row_count is not None else ""
        columns = f", {len(fact.columns)} columns" if fact.columns else ""
        facts = f"{fact.bytes} bytes{rows}{columns}, sha256 {fact.sha256[:16]}"
    else:
        facts = f"{len(hits.get(entry.key, []))} place(s) in the code"
    licence = entry.licence.value if entry.licence.value == UNKNOWN_LICENCE else f"{entry.licence.value} ([source]({entry.licence.evidence_url}))"
    return (f"| {entry.key} | {facts} | {licence} | {entry.personal_data} | "
            f"{entry.production_use}: {entry.production_reason.replace('|', '/')} |")


def clone_arguments(branch_or_tag: str, target_dir: Path, shallow: bool) -> list[str]:
    """WHAT: Builds the `git clone` argument list with core.autocrlf=false.
    WHY: On Windows git may convert LF to CRLF on checkout, which would change file sizes and SHA-256
    values; the audit must measure the bytes stored in git, the same on every machine.

    Args:
        branch_or_tag: branch or tag to clone.
        target_dir: folder to clone into.
        shallow: True for --depth 1, False for the full history.
    Returns:
        The argument list.
    Example:
        ("v1-final", Path("t"), True) -> ["git", "-c", "core.autocrlf=false", "clone", "--depth", "1", "--branch", "v1-final", URL, "t"]
    """
    depth = ["--depth", "1"] if shallow else []
    return ["git", "-c", "core.autocrlf=false", "clone"] + depth + ["--branch", branch_or_tag, V1_REPO_URL, str(target_dir)]


def clone_exact(branch_or_tag: str, target_dir: Path, shallow: bool) -> Path:
    """WHAT: Clones v1 into a new folder without line-ending conversion and returns the clone's root.
    WHY: A fresh clone every time (never reused), measuring exactly what git stores.

    Args:
        branch_or_tag: branch or tag to clone.
        target_dir: folder that must not exist.
        shallow: True for --depth 1, False for the full history.
    Returns:
        The clone's root folder.
    Raises:
        FileExistsError: if target_dir exists.
        RuntimeError: if git clone fails.
    Example:
        clone_exact("v1-legacy", Path("tmp/hist"), False) -> Path("tmp/hist") with all commits
    """
    if target_dir.exists():
        raise FileExistsError(f"refusing to reuse existing folder: {target_dir}")
    finished = run_command(clone_arguments(branch_or_tag, target_dir, shallow), None, GIT_TIMEOUT_SECONDS)
    if finished.returncode != 0:
        raise RuntimeError(f"git clone of {branch_or_tag} failed: {finished.stderr.strip()}")
    return target_dir


def clone_history(target_dir: Path) -> Path:
    """WHAT: Makes a full-history clone of the v1-legacy branch (no depth limit, no line-ending conversion).
    WHY: First-add commits can only be found with the whole history of the branch.

    Args:
        target_dir: folder that must not exist.
    Returns:
        The clone's root folder.
    Example:
        clone_history(Path("tmp/hist")) -> Path("tmp/hist") with all 382 commits of v1-legacy
    """
    return clone_exact("v1-legacy", target_dir, shallow=False)


def main(argv: list[str]) -> int:
    """WHAT: Clones v1-final (shallow) and v1-legacy (full history) into a temp folder, discovers files and
    hosts, measures every file, validates the YAML, runs the coverage and evidence checks, renders
    provenance.md (and measurements.json), and returns 1 if any check failed. The temp folder is always removed.
    WHY: One command proves the audit still matches the code and data at the tag.

    Args:
        argv: command-line arguments.
    Returns:
        0 when everything holds, otherwise 1.
    Side effects:
        Writes provenance.md and measurements.json (only when all checks pass).
    Example:
        main([]) with a complete provenance.yaml -> prints "OK: ..." and returns 0
    """
    parser = argparse.ArgumentParser(description="Audit v1's data files and external services.")
    parser.add_argument("--tag", default=V1_DEFAULT_TAG)
    parser.add_argument("--dir", default="docs/phase0/data-provenance")
    parser.add_argument("--out", default=None, help="folder to render into (default: the same as --dir); inputs are always read from --dir")
    args = parser.parse_args(argv)
    work_root = Path(tempfile.mkdtemp(prefix="data_provenance_"))
    try:
        v1_dir = clone_exact(args.tag, work_root / "v1", shallow=True) / "sentinel_flow_mvp"
        history_dir = clone_history(work_root / "history")
        out_directory = Path(args.out) if args.out else Path(args.dir)
        return audit(v1_dir, history_dir, Path(args.dir), out_directory)
    finally:
        shutil.rmtree(work_root, onerror=make_writable_and_retry)


def audit(v1_dir: Path, history_dir: Path, directory: Path, out_directory: Path) -> int:
    """WHAT: Runs discovery, measurement, validation, both checks and rendering for already-cloned v1.
    WHY: Separates the audit from the cloning so it is easier to read.

    Args:
        v1_dir: the sentinel_flow_mvp folder of the v1-final clone.
        history_dir: full-history clone of v1-legacy.
        directory: folder holding provenance.yaml (inputs are read only from here).
        out_directory: folder that receives provenance.md and measurements.json.
    Returns:
        0 when all checks pass, otherwise 1.
    Raises:
        ProvenanceError: for malformed YAML.
    Example:
        a consistent audit -> 0 and provenance.md written
    """
    entries = load_entries(directory / "provenance.yaml")
    files = discover_data_files(v1_dir)
    hits = discover_external_urls(v1_dir)
    problems = check_coverage(files + sorted(hits), entries) + check_evidence(entries, v1_dir, history_dir)
    for problem in problems:
        print("PROBLEM:", problem)
    if problems:
        return 1
    measurements = {path: measure_file(v1_dir / path) for path in files}
    out_directory.mkdir(parents=True, exist_ok=True)
    (out_directory / "provenance.md").write_text(render_provenance(entries, measurements, hits), encoding="utf-8")
    serializable = {path: dataclasses.asdict(measurement) for path, measurement in measurements.items()}
    (out_directory / "measurements.json").write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    print(f"OK: {len(files)} files and {len(hits)} hosts/libraries covered, all evidence verified")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
