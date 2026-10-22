# TERM: ADR - Architecture Decision Record: one decision, why, and what follows.
# TERM: carry-over decision - whether v2 keeps, rebuilds, drops or defers one item of v1.
# TERM: fixture - tiny in-memory artifacts built inside one test; no files, no network.
# TERM: dangling reference - an evidence pointer to a feature, test or action that does not exist.
"""Unit tests for tools/phase0/adr_000.py: coverage, rules, evidence references, phase map and rendering."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.phase0.adr_000 import (
    OWNER_DECISION_DATE,
    AdrError,
    Artifacts,
    Decision,
    Reference,
    check_coverage,
    check_evidence,
    check_rules,
    compute_headline,
    load_adr_field,
    load_decisions,
    parse_decision,
    render_adr,
    required_items,
)
from tools.phase0.data_provenance import UNKNOWN_LICENCE, Entry, Licence
from tools.phase0.feature_truth import Evidence, Feature, Finding

PHASES = {1: "Foundation", 3: "Identity", 5: "Billing core"}


def make_feature(feature_id: str, status: str, findings: list[Finding] | None = None) -> Feature:
    """WHAT: Builds a minimal Feature with the given status (and optional findings).
    WHY: Tests only need the id, status and findings.

    Args:
        feature_id: id.
        status: working, mocked, broken, missing or untested.
        findings: optional security findings.
    Returns:
        A Feature.
    Example:
        make_feature("f1", "mocked") -> Feature(id="f1", status="mocked", ...)
    """
    evidence = [Evidence(kind="code", file="a.py", line=1, contains="x")]
    return Feature(feature_id, "name", "billing", ["GET /a"], status, "", "", evidence, findings or [])


def make_entry(key: str, production_use: str) -> Entry:
    """WHAT: Builds a minimal provenance Entry with the given production use.
    WHY: Tests only need the key and production_use.

    Args:
        key: path or host.
        production_use: allowed, test-fixture-only, not-for-production or discard.
    Returns:
        An Entry.
    Example:
        make_entry("data/a.csv", "not-for-production") -> Entry(key="data/a.csv", ...)
    """
    return Entry(key, "dataset", [], Licence(UNKNOWN_LICENCE, "", "", ""), "no", "r", production_use, "r")


def make_artifacts(features: list[Feature]) -> Artifacts:
    """WHAT: Builds tiny Artifacts: the given features, one provenance file, one must_keep action, the test phase
    map and fixed test and flaw numbers.
    WHY: The ADR checks need all artifacts, but each test varies only the features.

    Args:
        features: the features.
    Returns:
        Artifacts.
    Example:
        make_artifacts([make_feature("f1", "working")]) -> Artifacts with 1 feature, 1 data item, 1 action
    """
    screens = {"pages": [{"route": "/pos", "user_actions": [{"name": "Search"}]}],
               "must_keep": [{"page": "/pos", "action": "Search", "feature": "f1"}], "rebuild_in_v2": [],
               "unused_calls": [{}, {}], "hard_coded_data": [{}]}
    summary = {"passed": 10, "failed": 2, "error": 1, "skipped": 0}
    return Artifacts(features, [make_entry("data/a.csv", "not-for-production")], screens, PHASES,
                     {"docker-with-db": summary, "docker-with-db-health-risk": {"passed": 7, "failed": 0, "error": 0, "skipped": 0}},
                     {"failed": 8, "passed": 1})


def decision(item: str, kind: str, choice: str, phase: int | None = 5, evidence: list[Reference] | None = None,
             security_phase: int | None = None) -> Decision:
    """WHAT: Builds a Decision with a default evidence reference.
    WHY: Keeps the test code short.

    Args:
        item: item id.
        kind: item kind.
        choice: keep, rebuild, drop or defer-v3.
        phase: v2 phase or None.
        evidence: references (default: one features.yaml reference).
        security_phase: optional security phase.
    Returns:
        A Decision.
    Example:
        decision("f1", "feature", "keep") -> Decision(item="f1", decision="keep", v2_phase=5, ...)
    """
    default = [Reference("artifact", {"artifact": "features.yaml", "id": item})]
    return Decision(item, kind, choice, "reason", phase, evidence or default, security_phase)


def full_decisions(choice_for_feature: str = "keep") -> list[Decision]:
    """WHAT: Builds a complete, valid decision list for make_artifacts([f1 working]): the feature, the data item,
    the screen action and every required module and service (dropped).
    WHY: Each test changes exactly one thing in an otherwise valid list.

    Args:
        choice_for_feature: decision for feature f1.
    Returns:
        The decision list.
    Example:
        full_decisions() -> 1 feature + 1 data + 1 action + 14 module/service decisions
    """
    artifacts = make_artifacts([make_feature("f1", "working")])
    owner = [Reference("owner-decision", {"type": "owner-decision", "date": OWNER_DECISION_DATE, "note": "n"})]
    decisions = [decision("f1", "feature", choice_for_feature), decision("data/a.csv", "data", "drop", None),
                 decision("/pos: Search", "screen-action", "rebuild")]
    for item, kind in required_items(artifacts).items():
        if kind in ("module", "service") and item != "data/a.csv":
            decisions.append(decision(item, kind, "drop", None, owner))
    return decisions


def test_uncovered_feature_and_duplicate_fail_coverage() -> None:
    """WHAT: Removing the decision for f1 reports "no decision for feature: f1"; adding a second decision for
    the data item reports "more than one decision"; a decision for an unknown item is reported.
    WHY: Nothing may be skipped or decided twice.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        no decision for f1 -> ["no decision for feature: f1"]
    """
    artifacts = make_artifacts([make_feature("f1", "working")])
    assert check_coverage(full_decisions(), artifacts) == []
    without = [each for each in full_decisions() if each.item != "f1"]
    assert "no decision for feature: f1" in check_coverage(without, artifacts)
    duplicated = full_decisions() + [decision("data/a.csv", "data", "drop", None)]
    assert "more than one decision for: data/a.csv" in check_coverage(duplicated, artifacts)
    assert "decision for an unknown item: ghost" in check_coverage(full_decisions() + [decision("ghost", "feature", "drop", None)], artifacts)


def test_keep_on_mocked_feature_and_on_not_for_production_data_are_rejected() -> None:
    """WHAT: "keep" on a mocked feature is rejected, "keep" on a working one is accepted, and "keep" on a data
    item marked not-for-production is rejected.
    WHY: Fake features and unusable data must never be carried over as if they were fine.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        keep on mocked f1 -> ["f1: a mocked feature cannot be 'keep' (only working ones can)"]
    """
    mocked = make_artifacts([make_feature("f1", "mocked")])
    assert check_rules(full_decisions("keep"), mocked) == ["f1: a mocked feature cannot be 'keep' (only working ones can)"]
    working = make_artifacts([make_feature("f1", "working")])
    assert check_rules(full_decisions("keep"), working) == []
    data_keep = [decision("data/a.csv", "data", "keep") if each.item == "data/a.csv" else each for each in full_decisions()]
    assert check_rules(data_keep, working) == ["data/a.csv: not-for-production items cannot be 'keep'"]


def test_security_finding_needs_an_early_rebuild_that_cites_it() -> None:
    """WHAT: A feature with a security finding is rejected if it is "keep", if its security_phase is later than
    Phase 3, or if the finding is not cited; it is accepted as a rebuild with security_phase 3 citing the finding.
    WHY: Every security finding must be fixed early and traceably.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        rebuild with security_phase 5 -> "... needs decision rebuild and security_phase <= 3"
    """
    finding = Finding("security", "no tenant filter", Evidence(kind="code", file="a.py", line=1, contains="x"))
    artifacts = make_artifacts([make_feature("f1", "working", [finding])])
    cite = Reference("artifact", {"artifact": "features.yaml", "id": "f1", "finding": "no tenant filter"})
    others = [each for each in full_decisions() if each.item != "f1"]
    good = others + [decision("f1", "feature", "rebuild", 5, [cite], security_phase=3)]
    assert check_rules(good, artifacts) == []
    late = others + [decision("f1", "feature", "rebuild", 5, [cite], security_phase=5)]
    assert any("security_phase <= 3" in problem for problem in check_rules(late, artifacts))
    uncited = others + [decision("f1", "feature", "rebuild", 5, None, security_phase=3)]
    assert any("security finding not cited: no tenant filter" in problem for problem in check_rules(uncited, artifacts))
    kept = others + [decision("f1", "feature", "keep", 5, [cite], security_phase=3)]
    assert any("needs decision rebuild" in problem for problem in check_rules(kept, artifacts))


def test_dropping_a_must_keep_action_needs_an_owner_reference() -> None:
    """WHAT: A must_keep screen action decided "drop" without an owner-decision reference is rejected, and
    accepted with one; an action whose feature is dropped must also be dropped.
    WHY: v2 must not silently lose a function the screens inventory said to keep.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        drop of "/pos: Search" without owner evidence -> "... can only be dropped with an owner-decision reference"
    """
    artifacts = make_artifacts([make_feature("f1", "working")])
    owner = [Reference("owner-decision", {"type": "owner-decision", "date": OWNER_DECISION_DATE, "note": "n"})]
    others = [each for each in full_decisions() if each.item != "/pos: Search"]
    bare = others + [decision("/pos: Search", "screen-action", "drop", None)]
    assert any("only be dropped with an owner-decision reference" in problem for problem in check_rules(bare, artifacts))
    backed = others + [decision("/pos: Search", "screen-action", "drop", None, owner)]
    assert check_rules(backed, artifacts) == []
    feature_dropped = [decision("f1", "feature", "drop", None) if each.item == "f1" else each for each in full_decisions()]
    assert any("its feature f1 is dropped" in problem for problem in check_rules(feature_dropped, artifacts))


def test_dangling_evidence_reference_is_rejected(tmp_path: Path) -> None:
    """WHAT: A reference to a feature id, a provenance key, a screen action or a test id that does not exist is
    reported; references to things that exist, and owner decisions, pass.
    WHY: A dangling reference is not evidence.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        {"artifact": "features.yaml", "id": "nope"} -> "x: features.yaml has no feature 'nope'"
    """
    artifacts = make_artifacts([make_feature("f1", "working")])
    good = [Reference("artifact", {"artifact": "features.yaml", "id": "f1"}),
            Reference("artifact", {"artifact": "provenance.yaml", "key": "data/a.csv"}),
            Reference("artifact", {"artifact": "screens.yaml", "page": "/pos", "action": "Search"}),
            Reference("owner-decision", {"type": "owner-decision", "date": OWNER_DECISION_DATE, "note": "n"})]
    bad = [Reference("artifact", {"artifact": "features.yaml", "id": "nope"}),
           Reference("artifact", {"artifact": "provenance.yaml", "key": "nope.csv"}),
           Reference("artifact", {"artifact": "screens.yaml", "page": "/pos", "action": "Ghost"}),
           Reference("artifact", {"artifact": "v1-test-results", "run": "r", "test_id": "t::missing"})]
    assert check_evidence([decision("x", "feature", "drop", None, good)], artifacts, tmp_path, tmp_path) == []
    problems = check_evidence([decision("x", "feature", "drop", None, bad)], artifacts, tmp_path, tmp_path)
    assert len(problems) == 4 and any("features.yaml has no feature 'nope'" in problem for problem in problems)


def test_unknown_phase_number_raises_an_error_naming_the_item() -> None:
    """WHAT: A decision whose v2_phase is not in the phase map, a keep without a phase, a drop with a phase and
    phase 0 for a rebuild are each rejected with the item name; a valid one parses.
    WHY: Every phase must come from the owner's phase map.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        v2_phase 99 -> AdrError("x: v2_phase 99 is not in the phase map (or is phase 0)")
    """
    evidence = [{"artifact": "features.yaml", "id": "x"}]
    base = {"item": "x", "kind": "feature", "decision": "rebuild", "reason": "r", "evidence": evidence, "v2_phase": 5}
    assert parse_decision(base, PHASES).v2_phase == 5
    with pytest.raises(AdrError, match="x: v2_phase 99 is not in the phase map"):
        parse_decision({**base, "v2_phase": 99}, PHASES)
    with pytest.raises(AdrError, match="x: v2_phase None is not in the phase map"):
        parse_decision({**base, "v2_phase": None}, PHASES)
    with pytest.raises(AdrError, match="x: v2_phase 0"):
        parse_decision({**base, "v2_phase": 0}, PHASES)
    with pytest.raises(AdrError, match="x: v2_phase must be null for drop"):
        parse_decision({**base, "decision": "drop"}, PHASES)


def test_render_headline_numbers_match_the_fixture_counts() -> None:
    """WHAT: Renders an ADR from the tiny artifacts (2 working, 1 mocked, 1 broken features; 8 flaws failed) and
    checks the computed headline numbers, the phase name next to its number, the Owner-to-confirm line and the
    standard ADR headings.
    WHY: Numbers in the ADR must come from the artifacts, never be typed.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        4 features -> the Context says "Features: 4 in total: 2 working, 1 mocked, 1 broken"
    """
    features = [make_feature("f1", "working"), make_feature("f2", "working"), make_feature("f3", "mocked"), make_feature("f4", "broken")]
    artifacts = make_artifacts(features)
    numbers = compute_headline(artifacts)
    assert (numbers["features"], numbers["status_working"], numbers["status_mocked"], numbers["status_broken"]) == (4, 2, 1, 1)
    decisions = full_decisions()
    decisions[0].confirm = "unclear which phase"
    owner = [{"text": "New UI design", "date": OWNER_DECISION_DATE}]
    text = render_adr(decisions, owner, artifacts, "2026-10-22")
    assert "Features: 4 in total: 2 working, 1 mocked, 1 broken, 0 missing, 0 untested" in text
    assert "docker-with-db baseline): 10 passed, 2 failed, 1 errors" in text
    assert "5 - Billing core" in text and "- f1: unclear which phase" in text
    for heading in ("## Status", "## Date", "## Context", "## Decision", "## Consequences", "## Alternatives considered", "## Why no benchmark", "## Owner to confirm"):
        assert heading in text


def write_decisions_file(tmp_path: Path, text: str) -> Path:
    """WHAT: Writes text to a decisions-000.yaml file inside the temp folder.
    WHY: The malformed-input tests must go through the real file loader.

    Args:
        tmp_path: pytest temporary folder.
        text: the YAML text.
    Returns:
        The file path.
    Example:
        write_decisions_file(tmp_path, "") -> tmp_path/decisions-000.yaml (empty)
    """
    path = tmp_path / "decisions-000.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_empty_file_and_missing_lists_raise_clear_errors(tmp_path: Path) -> None:
    """WHAT: An empty decisions file, a file without 'decisions', and a file without 'owner_decisions' each make the
    real loader raise AdrError naming the file; the date and status loaders also raise AdrError (not a crash) on
    an empty file, and reject an unknown status.
    WHY: Malformed input must stop with a clear message, never pass silently or crash with a Python error.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        an empty file -> AdrError("...decisions-000.yaml: expected 'owner_decisions' and 'decisions' lists")
    """
    empty = write_decisions_file(tmp_path, "")
    with pytest.raises(AdrError, match="decisions-000.yaml"):
        load_decisions(empty, PHASES)
    with pytest.raises(AdrError, match="missing adr_date"):
        load_adr_field(empty, "adr_date")
    no_decisions = write_decisions_file(tmp_path, "owner_decisions: []" + chr(10))
    with pytest.raises(AdrError, match="decisions-000.yaml"):
        load_decisions(no_decisions, PHASES)
    no_owner = write_decisions_file(tmp_path, "decisions: []" + chr(10))
    with pytest.raises(AdrError, match="decisions-000.yaml"):
        load_decisions(no_owner, PHASES)
    bad_status = write_decisions_file(tmp_path, "adr_status: Maybe" + chr(10))
    with pytest.raises(AdrError, match="adr_status must be one of"):
        load_adr_field(bad_status, "adr_status")


def valid_raw_decision() -> dict:
    """WHAT: Returns a valid raw decision mapping as it would come from the YAML.
    WHY: Each malformed-input test breaks exactly one field of it.

    Args:
        none.
    Returns:
        A dict that parse_decision accepts.
    Example:
        valid_raw_decision()["item"] -> "x"
    """
    return {"item": "x", "kind": "feature", "decision": "rebuild", "reason": "r", "v2_phase": 5,
            "evidence": [{"artifact": "features.yaml", "id": "x"}]}


def test_missing_fields_wrong_types_and_unknown_values_name_the_item() -> None:
    """WHAT: Each of these breaks one field of a valid decision and must raise AdrError that names item "x" (or
    describes the bad item when it has no usable name): missing kind, reason or evidence; an unknown decision
    value; v2_phase as a string, a float or a bool; security_phase as a string; a non-text reason; an evidence
    list that is a string; a non-text item.
    WHY: A wrong type (for example the bool True, which Python treats as phase 1) must never pass silently.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        v2_phase "5" -> AdrError("x: v2_phase '5' is not in the phase map (or is phase 0)")
    """
    assert parse_decision(valid_raw_decision(), PHASES).item == "x"
    breaks = [{"kind": None}, {"reason": None}, {"evidence": None}, {"decision": "maybe"}, {"v2_phase": "5"}, {"v2_phase": 5.0},
              {"v2_phase": True}, {"security_phase": "3"}, {"reason": ["r"]}, {"evidence": "features.yaml"}, {"confirm": 7}]
    for change in breaks:
        raw = {**valid_raw_decision(), **change}
        with pytest.raises(AdrError, match="x:"):
            parse_decision(raw, PHASES)
    for bad_item in (None, 5, "", ["x"]):
        with pytest.raises(AdrError, match="decision without a text item"):
            parse_decision({**valid_raw_decision(), "item": bad_item}, PHASES)
    without_item = {key: value for key, value in valid_raw_decision().items() if key != "item"}
    with pytest.raises(AdrError, match="decision without a text item"):
        parse_decision(without_item, PHASES)


def test_decision_for_a_nonexistent_item_is_reported_by_name(tmp_path: Path) -> None:
    """WHAT: A decisions file that decides an item no artifact has is loaded with the real loader, and the real
    coverage check reports that item by name (and the required item it displaced as undecided).
    WHY: A typo in an item id must not hide a missing decision.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        decision for "f-typo" -> "decision for an unknown item: f-typo"
    """
    text = ("owner_decisions: []" + chr(10) + "decisions:" + chr(10) + "- item: f-typo" + chr(10) + "  kind: feature" + chr(10)
            + "  decision: drop" + chr(10) + "  reason: r" + chr(10) + "  evidence:" + chr(10) + "  - {artifact: features.yaml, id: f1}" + chr(10))
    decisions, _ = load_decisions(write_decisions_file(tmp_path, text), PHASES)
    problems = check_coverage(decisions, make_artifacts([make_feature("f1", "working")]))
    assert "decision for an unknown item: f-typo" in problems
    assert "no decision for feature: f1" in problems
