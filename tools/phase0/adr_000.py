# TERM: ADR - Architecture Decision Record: a short document that states one decision, why, and what follows.
# TERM: carry-over decision - for one item of v1, whether v2 keeps it, rebuilds it, drops it or defers it to v3.
# TERM: phase map - the owner's list of numbered v2 build phases (docs/plan/phase-map.yaml).
# TERM: evidence reference - a pointer from a decision to a Phase 0 artifact (feature row, test result, code line ...).
# TERM: owner decision - a choice the owner already made; the ADR states it as given and does not re-argue it.
# TERM: security finding - a problem noted in features.yaml (for example a missing tenant filter).
"""Check the carry-over decisions for v1 (docs/adr/decisions-000.yaml) against the Phase 0 artifacts and render
ADR-000. Every v1 feature, data item, screen action, module and service needs exactly one decision, and every
decision must follow the evidence. Standard library plus PyYAML.

Meaning of the decisions (also printed in the ADR):
  keep        v2 carries the behaviour (and the tests that prove it) forward; allowed only for working features.
  rebuild     v2 builds it again, test-first, in the stated phase.
  drop        v2 does not have it.
  defer-v3    postponed to v3 by the owner.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

# The script is run as `python tools/phase0/adr_000.py`, so the repo root must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.phase0.data_provenance import Entry, clone_exact, load_entries  # noqa: E402
from tools.phase0.feature_truth import Feature, check_one_evidence, load_features, load_test_outcomes, parse_evidence  # noqa: E402
from tools.phase0.feature_truth import FeatureTruthError  # noqa: E402
from tools.phase0.run_v1_suite import V1_DEFAULT_TAG, make_writable_and_retry  # noqa: E402

DECISIONS = ["keep", "rebuild", "drop", "defer-v3"]
KINDS = ["feature", "data", "module", "service", "screen-action"]
REFERENCE_ARTIFACTS = ["features.yaml", "provenance.yaml", "screens.yaml", "v1-test-results", "scorecard-flaws"]
# policy: the owner gave these decisions on 2026-10-20/21; the exact day of each was not recorded.
OWNER_DECISION_DATE = "2026-10-20/21"
# policy: the owner also gave rulings on 2026-10-22 (the clinical safety judge and the accepted proposals).
OWNER_DECISION_DATES = [OWNER_DECISION_DATE, "2026-10-22"]
ADR_STATUSES = ["Proposed", "Accepted"]
# policy: "early" means built before the first user-facing app: identity and tenancy are Phase 3.
EARLY_PHASE_MAX = 3
# sourced: items that must have a decision besides features, data and screen actions. Each is a module path,
# compose service or package group named in the Task 7 brief.
REQUIRED_MODULES_AND_SERVICES = {
    "app/services/kafka_producer.py": "module", "redpanda": "service", "app/services/sgwa_service.py": "module",
    "app/api/v1/federated.py": "module", "app/services/compute_hypervisor.py": "module",
    "app/services/lora_training_worker.py": "module", "app/services/drift_service.py": "module",
    "app/services/regional_router.py": "module", "app/services/aegis_service.py": "module",
    "app/services/velocity_service.py": "module", "worker": "service", "celery-beat": "service",
    "frontend": "service", "requirements.txt:nvidia-*": "module"}


class AdrError(ValueError):
    """Raised when decisions-000.yaml or the phase map is malformed; the message names the item."""


@dataclasses.dataclass
class Reference:
    """One evidence reference. kind is "artifact", "code" or "owner-decision"; data holds its fields."""

    kind: str
    data: dict


@dataclasses.dataclass
class Decision:
    """The carry-over decision for one v1 item."""

    item: str
    kind: str
    decision: str
    reason: str
    v2_phase: int | None
    evidence: list[Reference]
    security_phase: int | None = None
    confirm: str = ""
    ruling: str = ""


@dataclasses.dataclass
class Artifacts:
    """The Phase 0 artifacts the checks and the render read."""

    features: list[Feature]
    provenance: list[Entry]
    screens: dict
    phases: dict[int, str]
    test_summaries: dict[str, dict]
    flaw_counts: dict[str, int]


def load_phase_map(path: Path) -> dict[int, str]:
    """WHAT: Reads the owner's phase map and returns {number: name}.
    WHY: Every v2_phase must be one of these numbers.

    Args:
        path: docs/plan/phase-map.yaml.
    Returns:
        Map of phase number to name.
    Raises:
        AdrError: for a malformed file or a repeated number.
    Example:
        a file with phases 0 and 1 -> {0: "Freeze and audit v1", 1: "Foundation, ..."}
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("phases"), list):
        raise AdrError(f"{path}: expected a mapping with a 'phases' list")
    phases: dict[int, str] = {}
    for entry in document["phases"]:
        number, name = entry.get("number"), entry.get("name")
        if not isinstance(number, int) or not name or number in phases:
            raise AdrError(f"{path}: bad or repeated phase entry: {entry!r}")
        phases[number] = name
    return phases


def parse_reference(raw: object, item: str) -> Reference:
    """WHAT: Validates one evidence reference: {artifact: ...}, {type: code, ...} or {type: owner-decision, date, note}.
    WHY: A reference that cannot be checked is not evidence.

    Args:
        raw: the YAML value.
        item: the decision's item, for error messages.
    Returns:
        Reference.
    Raises:
        AdrError: for an unknown shape, artifact name or owner-decision date.
    Example:
        {"artifact": "features.yaml", "id": "pos-checkout"} -> Reference("artifact", {...})
    """
    if not isinstance(raw, dict):
        raise AdrError(f"{item}: evidence entry is not a mapping: {raw!r}")
    if raw.get("type") == "owner-decision":
        if raw.get("date") not in OWNER_DECISION_DATES or not raw.get("note"):
            raise AdrError(f"{item}: owner-decision needs a date in {OWNER_DECISION_DATES} and a note")
        return Reference("owner-decision", raw)
    if raw.get("type") == "code":
        try:
            parse_evidence(raw, item)
        except FeatureTruthError as error:
            raise AdrError(str(error)) from error
        return Reference("code", raw)
    if raw.get("artifact") not in REFERENCE_ARTIFACTS:
        raise AdrError(f"{item}: evidence needs artifact in {REFERENCE_ARTIFACTS}, type code, or type owner-decision: {raw!r}")
    return Reference("artifact", raw)


def is_whole_number(value: object) -> bool:
    """WHAT: True for an int that is not a bool.
    WHY: In Python True == 1, so a bool would silently pass as phase 1.

    Args:
        value: any value.
    Returns:
        True only for real integers.
    Example:
        5 -> True; True -> False; "5" -> False
    """
    return isinstance(value, int) and not isinstance(value, bool)


def parse_decision(raw: object, phases: dict[int, str]) -> Decision:
    """WHAT: Validates one decision mapping against the phase map and the decision rules that need no other file:
    text item, reason, kind and decision values, a non-empty evidence list, text confirm/ruling, and v2_phase
    (a whole phase number from the map, 1 or later, for keep/rebuild; null for drop and defer-v3).
    WHY: Bad input stops here, naming the item, and a wrong type never passes silently.

    Args:
        raw: the YAML value.
        phases: the phase map.
    Returns:
        Decision.
    Raises:
        AdrError: naming the item.
    Example:
        {"item": "x", "decision": "keep", "v2_phase": "5", ...} -> AdrError("x: v2_phase '5' is not in the phase map (or is phase 0)")
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("item"), str) or not raw["item"]:
        raise AdrError(f"decision without a text item: {raw!r}")
    item = raw["item"]
    if raw.get("kind") not in KINDS or raw.get("decision") not in DECISIONS:
        raise AdrError(f"{item}: kind must be in {KINDS} and decision in {DECISIONS}")
    if not isinstance(raw.get("reason"), str) or not raw["reason"] or not isinstance(raw.get("evidence"), list) or not raw["evidence"]:
        raise AdrError(f"{item}: needs a text reason and a non-empty evidence list")
    for text_field in ("confirm", "ruling"):
        if raw.get(text_field) is not None and not isinstance(raw[text_field], str):
            raise AdrError(f"{item}: {text_field} must be text")
    phase = raw.get("v2_phase")
    builds = raw["decision"] in ("keep", "rebuild")
    if builds and (not is_whole_number(phase) or phase not in phases or phase == 0):
        raise AdrError(f"{item}: v2_phase {phase!r} is not in the phase map (or is phase 0)")
    if not builds and phase is not None:
        raise AdrError(f"{item}: v2_phase must be null for {raw['decision']}")
    security = raw.get("security_phase")
    if security is not None and (not is_whole_number(security) or security not in phases):
        raise AdrError(f"{item}: security_phase {security!r} is not in the phase map")
    references = [parse_reference(entry, item) for entry in raw["evidence"]]
    return Decision(item, raw["kind"], raw["decision"], raw["reason"], phase, references, security,
                    raw.get("confirm") or "", raw.get("ruling") or "")


def load_decisions(path: Path, phases: dict[int, str]) -> tuple[list[Decision], list[dict]]:
    """WHAT: Reads decisions-000.yaml: the given owner decisions and the list of carry-over decisions.
    WHY: One validated source for the checks and the render.

    Args:
        path: the YAML file.
        phases: the phase map.
    Returns:
        (decisions, owner_decisions as plain dicts).
    Raises:
        AdrError: for a malformed file or entry.
    Example:
        a file with 2 decisions and 1 owner decision -> ([Decision, Decision], [{...}])
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("decisions"), list) or not isinstance(document.get("owner_decisions"), list):
        raise AdrError(f"{path}: expected 'owner_decisions' and 'decisions' lists")
    return [parse_decision(raw, phases) for raw in document["decisions"]], document["owner_decisions"]


def load_adr_field(path: Path, key: str) -> str:
    """WHAT: Reads one required top-level text field (adr_date or adr_status) from decisions-000.yaml.
    WHY: These are recorded facts of the decision file, not something the renderer guesses; an empty or
    malformed file must give a clear error, not a crash.

    Args:
        path: the YAML file.
        key: "adr_date" or "adr_status".
    Returns:
        The value as text.
    Raises:
        AdrError: if the file is not a mapping or the key is missing; for adr_status also if the value is unknown.
    Example:
        adr_status: Accepted -> "Accepted"
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not document.get(key):
        raise AdrError(f"{path}: missing {key}")
    value = str(document[key])
    if key == "adr_status" and value not in ADR_STATUSES:
        raise AdrError(f"{path}: adr_status must be one of {ADR_STATUSES}")
    return value


def screen_items(screens: dict) -> dict[str, str]:
    """WHAT: Lists every screen-action item ("<page>: <action>") from screens.yaml's must_keep and rebuild_in_v2
    lists, mapped to the feature id each one names.
    WHY: Each such action needs exactly one decision.

    Args:
        screens: parsed screens.yaml.
    Returns:
        Map item -> feature id.
    Example:
        a must_keep entry (page /pos, action "Search", feature inventory-search) -> {"/pos: Search": "inventory-search"}
    """
    items: dict[str, str] = {}
    for list_name in ("must_keep", "rebuild_in_v2"):
        for entry in screens[list_name]:
            items[f"{entry['page']}: {entry['action']}"] = entry["feature"]
    return items


def required_items(artifacts: Artifacts) -> dict[str, str]:
    """WHAT: Builds the full set of items that need a decision: every feature id, every provenance item, every
    must_keep / rebuild_in_v2 action, and the listed modules and services. Value = the item's kind.
    WHY: Coverage is checked against this one set.

    Args:
        artifacts: the loaded artifacts.
    Returns:
        Map item -> kind.
    Example:
        2 features, 1 provenance file, 1 action, plus the fixed module list -> that many entries
    """
    items = {feature.id: "feature" for feature in artifacts.features}
    for entry in artifacts.provenance:
        items[entry.key] = "service" if entry.kind == "external-api" else "data"
    items.update({item: "screen-action" for item in screen_items(artifacts.screens)})
    items.update(REQUIRED_MODULES_AND_SERVICES)
    return items


def check_coverage(decisions: list[Decision], artifacts: Artifacts) -> list[str]:
    """WHAT: Checks every required item has exactly one decision of the right kind, and no decision is for an
    unknown item.
    WHY: Nothing may be silently skipped or decided twice.

    Args:
        decisions: loaded decisions.
        artifacts: the loaded artifacts.
    Returns:
        A list of problems (empty when covered exactly once).
    Example:
        a feature with no decision -> ["no decision for feature: pos-history"]
    """
    required = required_items(artifacts)
    counts: dict[str, int] = {}
    for decision in decisions:
        counts[decision.item] = counts.get(decision.item, 0) + 1
    problems: list[str] = []
    for item, kind in sorted(required.items()):
        if counts.get(item, 0) == 0:
            problems.append(f"no decision for {kind}: {item}")
    for decision in decisions:
        if decision.item not in required:
            problems.append(f"decision for an unknown item: {decision.item}")
        elif required[decision.item] != decision.kind:
            problems.append(f"{decision.item}: kind should be {required[decision.item]}, not {decision.kind}")
    problems += [f"more than one decision for: {item}" for item, count in sorted(counts.items()) if count > 1]
    return problems


def has_owner_reference(decision: Decision) -> bool:
    """WHAT: True when the decision cites an owner-decision reference.
    WHY: Dropping a must_keep action is only allowed on the owner's say-so.

    Args:
        decision: a decision.
    Returns:
        True if any reference is an owner-decision.
    Example:
        evidence [{"type": "owner-decision", ...}] -> True
    """
    return any(reference.kind == "owner-decision" for reference in decision.evidence)


def check_feature_rules(decision: Decision, artifacts: Artifacts) -> list[str]:
    """WHAT: Rules for a feature decision: "keep" only for a working feature (mocked and missing can never be
    kept); a feature with security findings must be a rebuild with security_phase in an early phase, and every
    finding must be cited.
    WHY: Decisions follow the evidence from the feature table.

    Args:
        decision: a decision of kind feature.
        artifacts: the loaded artifacts.
    Returns:
        Problems.
    Example:
        keep on a mocked feature -> ["auto-po: a mocked feature cannot be 'keep'"]
    """
    feature = None
    for candidate in artifacts.features:
        if candidate.id == decision.item:
            feature = candidate
            break
    if feature is None:
        return []
    problems: list[str] = []
    if decision.decision == "keep" and feature.status != "working":
        problems.append(f"{decision.item}: a {feature.status} feature cannot be 'keep' (only working ones can)")
    if feature.findings:
        if decision.decision != "rebuild" or decision.security_phase is None or decision.security_phase > EARLY_PHASE_MAX:
            problems.append(f"{decision.item}: has security findings, so it needs decision rebuild and security_phase <= {EARLY_PHASE_MAX}")
        cited_titles: list[str] = []
        for reference in decision.evidence:
            is_own_artifact_reference = reference.kind == "artifact" and reference.data.get("id") == decision.item
            if is_own_artifact_reference and reference.data.get("finding"):
                cited_titles.append(reference.data["finding"])
        for finding in feature.findings:
            if finding.title not in cited_titles:
                problems.append(f"{decision.item}: security finding not cited: {finding.title}")
    return problems


def check_rules(decisions: list[Decision], artifacts: Artifacts) -> list[str]:
    """WHAT: Applies the decision rules: feature rules (see check_feature_rules); data marked not-for-production
    or discard cannot be "keep"; a screen action must not be decided differently from its feature when that
    feature is dropped, and a must_keep action may only be dropped on an owner-decision reference.
    WHY: The rules keep decisions honest about what v1 really is.

    Args:
        decisions: loaded decisions.
        artifacts: the loaded artifacts.
    Returns:
        Problems.
    Example:
        keep on a not-for-production CSV -> ["data/x.csv: not-for-production data cannot be 'keep'"]
    """
    by_item = {decision.item: decision for decision in decisions}
    production = {entry.key: entry.production_use for entry in artifacts.provenance}
    actions = screen_items(artifacts.screens)
    must_keep_items = {f"{entry['page']}: {entry['action']}" for entry in artifacts.screens["must_keep"]}
    problems: list[str] = []
    for decision in decisions:
        if decision.kind == "feature":
            problems += check_feature_rules(decision, artifacts)
        if decision.kind in ("data", "service") and decision.decision == "keep" and production.get(decision.item) in ("not-for-production", "discard"):
            problems.append(f"{decision.item}: {production[decision.item]} items cannot be 'keep'")
        if decision.kind == "screen-action":
            feature_decision = by_item.get(actions.get(decision.item, ""))
            if feature_decision is not None and feature_decision.decision == "drop" and decision.decision != "drop":
                problems.append(f"{decision.item}: its feature {feature_decision.item} is dropped, so the action must be dropped too")
            if decision.item in must_keep_items and decision.decision == "drop" and not has_owner_reference(decision):
                problems.append(f"{decision.item}: a must_keep action can only be dropped with an owner-decision reference")
    return problems


def check_reference(decision: Decision, reference: Reference, artifacts: Artifacts, v1_dir: Path, results_dir: Path) -> str | None:
    """WHAT: Resolves one evidence reference: code items against the v1 clone; artifact items against the
    named artifact (feature id, provenance key, screen page and action, test id in a run, or text in a run file).
    WHY: A dangling reference is not evidence.

    Args:
        decision: the owning decision.
        reference: the reference.
        artifacts: the loaded artifacts.
        v1_dir: the sentinel_flow_mvp folder of a fresh v1-final clone.
        results_dir: docs/phase0/v1-test-results.
    Returns:
        A problem text or None.
    Example:
        {"artifact": "features.yaml", "id": "nope"} -> "x: features.yaml has no feature 'nope'"
    """
    if reference.kind == "owner-decision":
        return None
    if reference.kind == "code":
        return check_one_evidence(decision.item, parse_evidence(reference.data, decision.item), v1_dir, results_dir, {})
    data = reference.data
    name = data["artifact"]
    if name == "features.yaml":
        return None if any(feature.id == data.get("id") for feature in artifacts.features) else f"{decision.item}: features.yaml has no feature '{data.get('id')}'"
    if name == "provenance.yaml":
        return None if any(entry.key == data.get("key") for entry in artifacts.provenance) else f"{decision.item}: provenance.yaml has no item '{data.get('key')}'"
    if name == "screens.yaml":
        return check_screen_reference(decision, data, artifacts)
    return check_test_reference(decision, data, results_dir)


def check_screen_reference(decision: Decision, data: dict, artifacts: Artifacts) -> str | None:
    """WHAT: Checks a {artifact: screens.yaml, page, action} reference names an action that exists on that page.
    WHY: Part of the dangling-reference check.

    Args:
        decision: the owning decision.
        data: the reference fields.
        artifacts: the loaded artifacts.
    Returns:
        A problem text or None.
    Example:
        page "/pos" with an action that does not exist -> "x: screens.yaml has no action '...' on page /pos"
    """
    for page in artifacts.screens["pages"]:
        names = {action.get("name") for action in page.get("user_actions") or []}
        if page.get("route") == data.get("page") and data.get("action") in names:
            return None
    return f"{decision.item}: screens.yaml has no action '{data.get('action')}' on page {data.get('page')}"


def check_test_reference(decision: Decision, data: dict, results_dir: Path) -> str | None:
    """WHAT: Checks a v1-test-results or scorecard-flaws reference: either a test_id that exists in that run, or
    a file in the run folder that contains the given text.
    WHY: Part of the dangling-reference check; test runs are the strongest evidence.

    Args:
        decision: the owning decision.
        data: the reference fields (run plus test_id, or run plus file plus contains).
        results_dir: docs/phase0/v1-test-results.
    Returns:
        A problem text or None.
    Example:
        run "docker-with-db", test_id present -> None; a missing test_id -> "x: test ... not found in run ..."
    """
    run = "../scorecard-flaws" if data["artifact"] == "scorecard-flaws" else data.get("run", "")
    if data.get("test_id"):
        outcomes = load_test_outcomes(results_dir, run)
        return None if data["test_id"] in outcomes else f"{decision.item}: test {data['test_id']} not found in run {run}"
    target = results_dir / run / str(data.get("file", ""))
    if target.is_file() and str(data.get("contains", "")) in target.read_text(encoding="utf-8", errors="replace"):
        return None
    return f"{decision.item}: {data.get('file')} in run {run} does not contain {data.get('contains')!r}"


def check_evidence(decisions: list[Decision], artifacts: Artifacts, v1_dir: Path, results_dir: Path) -> list[str]:
    """WHAT: Resolves every evidence reference of every decision and returns ALL problems.
    WHY: A reviewer should fix every dangling reference in one pass.

    Args:
        decisions: loaded decisions.
        artifacts: the loaded artifacts.
        v1_dir: the sentinel_flow_mvp folder of a fresh v1-final clone.
        results_dir: docs/phase0/v1-test-results.
    Returns:
        Problems.
    Example:
        one reference to a missing feature id -> a list with one problem
    """
    problems: list[str] = []
    for decision in decisions:
        for reference in decision.evidence:
            problem = check_reference(decision, reference, artifacts, v1_dir, results_dir)
            if problem is not None:
                problems.append(problem)
    return problems


def compute_headline(artifacts: Artifacts) -> dict[str, int]:
    """WHAT: Computes the headline numbers for the ADR's Context from the artifacts only: feature counts by
    status, security findings, data items by production use, screen counts and the test baseline figures.
    WHY: No number in the ADR is typed by hand.

    Args:
        artifacts: the loaded artifacts.
    Returns:
        Map of name -> number.
    Example:
        3 working + 1 mocked features -> {"features": 4, "status_working": 3, "status_mocked": 1, ...}
    """
    numbers: dict[str, int] = {"features": len(artifacts.features)}
    for status in ("working", "mocked", "broken", "missing", "untested"):
        numbers[f"status_{status}"] = sum(1 for feature in artifacts.features if feature.status == status)
    numbers["security_findings"] = sum(len(feature.findings) for feature in artifacts.features)
    for use in ("allowed", "test-fixture-only", "not-for-production", "discard"):
        numbers[f"data_{use}"] = sum(1 for entry in artifacts.provenance if entry.production_use == use)
    numbers["data_items"] = len(artifacts.provenance)
    numbers["pages"] = len(artifacts.screens["pages"])
    numbers["must_keep_actions"] = len(artifacts.screens["must_keep"])
    numbers["rebuild_actions"] = len(artifacts.screens["rebuild_in_v2"])
    numbers["unused_calls"] = len(artifacts.screens["unused_calls"])
    numbers["hard_coded_items"] = len(artifacts.screens["hard_coded_data"])
    for run, summary in artifacts.test_summaries.items():
        for outcome in ("passed", "failed", "error", "skipped"):
            numbers[f"{run}_{outcome}"] = summary[outcome]
    numbers["flaws_failed_as_designed"] = artifacts.flaw_counts["failed"]
    numbers["flaw_controls_passed"] = artifacts.flaw_counts["passed"]
    return numbers


def phase_label(number: int | None, phases: dict[int, str]) -> str:
    """WHAT: Renders a phase as "3 - Identity, roles, three-app permissions", or "-" for none.
    WHY: The ADR shows the phase name next to its number.

    Args:
        number: phase number or None.
        phases: the phase map.
    Returns:
        The label.
    Example:
        3 -> "3 - Identity, roles, three-app permissions"; None -> "-"
    """
    return "-" if number is None else f"{number} - {phases[number]}"


def reference_text(reference: Reference) -> str:
    """WHAT: Renders one evidence reference as a short Markdown string (with a relative link to the artifact).
    WHY: Every table row must show where its evidence lives.

    Args:
        reference: the reference.
    Returns:
        Markdown text.
    Example:
        {"artifact": "features.yaml", "id": "pos-checkout"} -> "[features.yaml#pos-checkout](../phase0/feature-truth/features.yaml)"
    """
    data = reference.data
    links = {"features.yaml": "../phase0/feature-truth/features.yaml", "provenance.yaml": "../phase0/data-provenance/provenance.yaml",
             "screens.yaml": "../phase0/screens/screens.yaml", "v1-test-results": "../phase0/v1-test-results/",
             "scorecard-flaws": "../phase0/scorecard-flaws/results.md"}
    if reference.kind == "owner-decision":
        return f"owner-decision {data['date']}"
    if reference.kind == "code":
        return f"v1 {data['file']}:{data['line']}"
    detail = data.get("id") or data.get("key") or data.get("test_id") or f"{data.get('page', '')} {data.get('action', '')}".strip() or data.get("file", "")
    return f"[{data['artifact']}#{detail}]({links[data['artifact']]})"


def render_decision_table(decisions: list[Decision], which: str, phases: dict[int, str]) -> list[str]:
    """WHAT: Renders the table of one decision type (keep, rebuild, drop or defer-v3): item, kind, reason,
    v2 phase and evidence links.
    WHY: The Decision section of the ADR.

    Args:
        decisions: all decisions.
        which: "keep", "rebuild", "drop" or "defer-v3".
        phases: the phase map.
    Returns:
        Markdown lines.
    Example:
        2 keep decisions -> a heading and a 2-row table
    """
    chosen = [decision for decision in decisions if decision.decision == which]
    lines = ["", f"### {which} ({len(chosen)})", ""]
    if not chosen:
        return lines + ["None from v1. (Deferrals with no v1 counterpart are listed under the given owner decisions.)"]
    lines += ["| item | kind | reason | v2 phase | evidence |", "|---|---|---|---|---|"]
    for decision in sorted(chosen, key=lambda each: (each.v2_phase if each.v2_phase is not None else 99, each.item)):
        evidence = "<br>".join(reference_text(reference) for reference in decision.evidence)
        reason = decision.reason.replace("|", "/")
        lines.append(f"| {decision.item.replace('|', '/')} | {decision.kind} | {reason} | {phase_label(decision.v2_phase, phases)} | {evidence} |")
    return lines


def render_context(artifacts: Artifacts, owner_decisions: list[dict]) -> list[str]:
    """WHAT: Renders the Context section: the headline numbers computed from the artifacts and the owner
    decisions that were given.
    WHY: The reader sees what Phase 0 found, with numbers that cannot drift from the artifacts.

    Args:
        artifacts: the loaded artifacts.
        owner_decisions: the given owner decisions.
    Returns:
        Markdown lines.
    Example:
        64 features -> "- 64 features ..." line with the status split
    """
    n = compute_headline(artifacts)
    lines = ["## Context", "", "Phase 0 froze and audited v1 (prv3 tag v1-final). What it found, from the checked artifacts:", "",
             f"- Features: {n['features']} in total: {n['status_working']} working, {n['status_mocked']} mocked, {n['status_broken']} broken, "
             f"{n['status_missing']} missing, {n['status_untested']} untested. {n['security_findings']} security findings are recorded.",
             f"- Tests (docker-with-db baseline): {n['docker-with-db_passed']} passed, {n['docker-with-db_failed']} failed, "
             f"{n['docker-with-db_error']} errors, {n['docker-with-db_skipped']} skipped. The health_risk suite adds {n['docker-with-db-health-risk_passed']} passed.",
             f"- Vendor scorecard: {n['flaws_failed_as_designed']} designed flaw tests fail on v1 as intended; {n['flaw_controls_passed']} positive control passes.",
             f"- Data: {n['data_items']} items audited; {n['data_allowed']} allowed for production, {n['data_test-fixture-only']} test-fixture-only, "
             f"{n['data_not-for-production']} not-for-production, {n['data_discard']} to discard.",
             f"- Screens: {n['pages']} pages; {n['must_keep_actions']} actions to keep, {n['rebuild_actions']} to rebuild properly; "
             f"{n['unused_calls']} frontend calls reach no screen; {n['hard_coded_items']} hard-coded items in the UI.", "",
             "Given owner decisions (stated as given, not re-argued here):", ""]
    lines += [f"- {entry['text']} ({entry['date']})" for entry in owner_decisions]
    return lines


def render_consequences(decisions: list[Decision], artifacts: Artifacts) -> list[str]:
    """WHAT: Renders the Consequences section: how many items each phase must build, the security findings that
    force Phase 3 first, and what the drops free v2 from.
    WHY: Shows what v2 must build first and why, from the decisions themselves.

    Args:
        decisions: all decisions.
        artifacts: the loaded artifacts.
    Returns:
        Markdown lines.
    Example:
        3 items in phase 5 -> a row "5 - Billing core | 3"
    """
    counts: dict[int, int] = {}
    for decision in decisions:
        if decision.v2_phase is not None:
            counts[decision.v2_phase] = counts.get(decision.v2_phase, 0) + 1
    lines = ["## Consequences", "", "Items each v2 phase must build or rebuild (keep and rebuild decisions):", "", "| phase | items |", "|---|---|"]
    lines += [f"| {phase_label(number, artifacts.phases)} | {counts[number]} |" for number in sorted(counts)]
    secure = [decision for decision in decisions if decision.security_phase is not None]
    lines += ["", f"Identity and tenancy come first ({phase_label(EARLY_PHASE_MAX, artifacts.phases)}) because {len(secure)} features carry "
              f"{compute_headline(artifacts)['security_findings']} security findings:", ""]
    lines += [f"- {decision.item}: " + "; ".join(reference.data["finding"] for reference in decision.evidence if reference.data.get("finding")) for decision in secure]
    drops = [decision for decision in decisions if decision.decision == "drop"]
    lines += ["", f"{len(drops)} items are dropped, so v2 starts without the Kafka event mesh, federated aggregation, the compute hypervisor, "
              "the CUDA package stack and the generated files and logs listed above."]
    return lines


def render_alternatives(artifacts: Artifacts) -> list[str]:
    """WHAT: Renders the Alternatives considered section: "rewrite v1 in place" versus "fresh repo", with the
    evidence from the numbers.
    WHY: A decision record states what else was possible and why it was not chosen.

    Args:
        artifacts: the loaded artifacts.
    Returns:
        Markdown lines.
    Example:
        39 untested + 5 broken features -> those counts appear in the 'rewrite in place' argument
    """
    n = compute_headline(artifacts)
    return ["## Alternatives considered", "",
            "1. **Rewrite v1 in place.** Evidence against: " + f"only {n['status_working']} of {n['features']} features are proven working, "
            f"{n['status_untested']} have no test, {n['status_mocked']} are mocked and {n['status_missing']} are missing; "
            f"the baseline suite has {n['docker-with-db_failed']} failures and {n['docker-with-db_error']} errors; "
            f"the vendor scorecard has {n['flaws_failed_as_designed']} proven flaws; and v1's requirements do not install on Windows. "
            "An in-place rewrite would have to fix, test and re-secure almost everything while keeping a fragile base running.",
            "", "2. **Fresh repo (chosen).** v2 starts empty. v1 stays frozen and read-only as a reference. The checked Phase 0 artifacts "
            "decide what v2 carries over: working behaviour and its tests are kept as the specification, everything else is rebuilt test-first "
            "or dropped, so nothing is copied across unexamined."]


def render_adr(decisions: list[Decision], owner_decisions: list[dict], artifacts: Artifacts, adr_date: str, status: str = "Proposed") -> str:
    """WHAT: Renders ADR-000 in standard ADR form: Title, Status (Proposed), Context, Decision (tables), Consequences,
    Alternatives considered, Why no benchmark, and the Owner to confirm list.
    WHY: The final, human-readable record; generated, never edited by hand.

    Args:
        decisions: all decisions.
        owner_decisions: the given owner decisions.
        artifacts: the loaded artifacts.
        adr_date: the date written under "Date" (from decisions-000.yaml).
        status: "Proposed" or "Accepted" (from decisions-000.yaml).
    Returns:
        Markdown text.
    Example:
        decisions plus artifacts -> text starting "# ADR-000: What v2 carries over from v1"
    """
    lines = ["# ADR-000: What v2 carries over from v1", "", "_Generated by tools/phase0/adr_000.py from docs/adr/decisions-000.yaml. Do not edit by hand._",
             "", "## Status", "", status, "", "## Date", "", adr_date, ""]
    lines += render_context(artifacts, owner_decisions)
    lines += ["", "## Decision", "", "Meaning: **keep** = v2 carries the behaviour and the tests that prove it (only for working features); "
              "**rebuild** = v2 builds it again, test-first, in the stated phase; **drop** = v2 does not have it; **defer-v3** = postponed by the owner."]
    for which in DECISIONS:
        lines += render_decision_table(decisions, which, artifacts.phases)
    lines += [""] + render_consequences(decisions, artifacts) + [""] + render_alternatives(artifacts)
    lines += ["", "## Why no benchmark", "", "This ADR is a scope decision (what to carry over), not a technology choice, so there is nothing to benchmark. "
              "The benchmark-before-choosing rule applies from ADR-001 onward.", "", "## Owner rulings", ""]
    lines += [f"- {decision.item}: {decision.ruling}" for decision in decisions if decision.ruling] or ["None."]
    lines += ["", "## Owner to confirm", ""]
    lines += [f"- {decision.item}: {decision.confirm}" for decision in decisions if decision.confirm and not decision.ruling] or ["None outstanding."]
    return chr(10).join(lines) + chr(10)


def load_artifacts(root: Path) -> Artifacts:
    """WHAT: Loads every Phase 0 artifact the ADR depends on from the medoRa repo folder.
    WHY: One loader keeps main() short.

    Args:
        root: the medoRa repo root.
    Returns:
        Artifacts.
    Raises:
        AdrError, ProvenanceError, FeatureTruthError: for malformed inputs.
    Example:
        the real repo -> Artifacts with 64 features and 34 provenance items
    """
    docs = root / "docs"
    summaries = {}
    for run in ("docker-with-db", "docker-with-db-health-risk"):
        summaries[run] = json.loads((docs / "phase0/v1-test-results" / run / "results.json").read_text(encoding="utf-8"))["summary"]
    flaw_text = (docs / "phase0/scorecard-flaws/results.md").read_text(encoding="utf-8")
    flaws = {"failed": flaw_text.count("| failed |"), "passed": 1 if "Positive control (test_positive_control): passed" in flaw_text else 0}
    return Artifacts(load_features(docs / "phase0/feature-truth/features.yaml"), load_entries(docs / "phase0/data-provenance/provenance.yaml"),
                     yaml.safe_load((docs / "phase0/screens/screens.yaml").read_text(encoding="utf-8")),
                     load_phase_map(docs / "plan/phase-map.yaml"), summaries, flaws)


def audit(root: Path, v1_dir: Path, out_directory: Path) -> int:
    """WHAT: Loads everything, runs coverage, rule and evidence checks, and (only if all pass) renders the ADR.
    WHY: Separates the audit from cloning.

    Args:
        root: the medoRa repo root.
        v1_dir: the sentinel_flow_mvp folder of a fresh v1-final clone.
        out_directory: folder that receives ADR-000-v1-carry-over.md (inputs are read only from root/docs).
    Returns:
        0 when everything holds, otherwise 1.
    Side effects:
        Writes ADR-000-v1-carry-over.md into out_directory when all checks pass.
    Example:
        a consistent decisions file -> prints "OK: ..." and returns 0
    """
    artifacts = load_artifacts(root)
    decisions, owner_decisions = load_decisions(root / "docs/adr/decisions-000.yaml", artifacts.phases)
    problems = check_coverage(decisions, artifacts) + check_rules(decisions, artifacts)
    problems += check_evidence(decisions, artifacts, v1_dir, root / "docs/phase0/v1-test-results")
    for problem in problems:
        print("PROBLEM:", problem)
    if problems:
        return 1
    adr_date = load_adr_field(root / "docs/adr/decisions-000.yaml", "adr_date")
    adr_status = load_adr_field(root / "docs/adr/decisions-000.yaml", "adr_status")
    adr_text = render_adr(decisions, owner_decisions, artifacts, adr_date, adr_status)
    out_directory.mkdir(parents=True, exist_ok=True)
    (out_directory / "ADR-000-v1-carry-over.md").write_text(adr_text, encoding="utf-8")
    print(f"OK: {len(decisions)} decisions cover every feature, data item, screen action, module and service; all evidence verified")
    return 0


def main(argv: list[str]) -> int:
    """WHAT: Clones v1-final fresh (line-ending conversion off) into a temp folder, runs the audit and returns its
    exit code. The temp folder is always removed.
    WHY: Code evidence is verified against the tagged code.

    Args:
        argv: command-line arguments.
    Returns:
        0 when everything holds, otherwise 1.
    Example:
        main([]) with a consistent decisions file -> 0
    """
    parser = argparse.ArgumentParser(description="Check and render ADR-000.")
    parser.add_argument("--tag", default=V1_DEFAULT_TAG)
    parser.add_argument("--out", default=None, help="folder to render the ADR into (default: docs/adr); inputs are always read from docs/")
    args = parser.parse_args(argv)
    work_root = Path(tempfile.mkdtemp(prefix="adr_000_"))
    try:
        v1_dir = clone_exact(args.tag, work_root / "v1", shallow=True) / "sentinel_flow_mvp"
        root = Path.cwd()
        out_directory = Path(args.out) if args.out else root / "docs/adr"
        return audit(root, v1_dir, out_directory)
    finally:
        shutil.rmtree(work_root, onerror=make_writable_and_retry)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
