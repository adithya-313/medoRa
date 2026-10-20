# TERM: inventory - the machine-found list of routes, tasks, service/worker files and pages.
# TERM: entry point - the route, task, file or page through which a feature is reached.
# TERM: evidence - a citation (test result, code line or document text) that a script can re-check.
# TERM: fixture tree - a tiny fake v1 folder written into pytest's temp folder for one test.
"""Unit tests for tools/phase0/feature_truth.py. Tiny fixture files only; no network, no git, no Docker."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.phase0.feature_truth import (
    Evidence,
    Feature,
    FeatureTruthError,
    Finding,
    InventoryItem,
    check_coverage,
    check_evidence,
    discover_inventory,
    parse_feature,
    render_table,
)

MAIN_PY = (
    "from app.api import good\n"
    "def create(app):\n"
    "    app.include_router(good.router, prefix='/api/v1/good')\n"
)
GOOD_PY = (
    "router = APIRouter(prefix='/sub')\n"
    "@router.get('/a')\n"
    "def read_a():\n"
    "    return 1\n"
    "# @router.get('/commented_out')\n"
    "@router.post(\n"
    "    '/b',\n"
    "    status_code=202,\n"
    ")\n"
    "def write_b():\n"
    "    return 2\n"
)
UNMOUNTED_PY = "@router.get('/x')\ndef lonely():\n    return 3\n"
TASK_PY = (
    "@celery_app.task(name='named_task')\n"
    "def named():\n"
    "    return 1\n"
    "@shared_task(bind=True)\n"
    "def unnamed(self):\n"
    "    return 2\n"
)


def build_fixture_v1(root: Path) -> Path:
    """WHAT: Writes a tiny fake sentinel_flow_mvp tree: a main.py that mounts one router, that router's
    file (two routes, one commented-out decorator), an unmounted router file, a task file, one service
    file, one worker file and one page.
    WHY: Discovery tests need real files but must not depend on the real v1 clone.

    Args:
        root: an empty folder.
    Returns:
        The fake sentinel_flow_mvp folder.
    Example:
        build_fixture_v1(tmp_path) -> tmp_path (with app/main.py, app/api/good.py, ...)
    """
    files = {
        "app/main.py": MAIN_PY,
        "app/api/good.py": GOOD_PY,
        "app/api/unmounted.py": UNMOUNTED_PY,
        "app/tasks/jobs.py": TASK_PY,
        "app/services/thing.py": "x = 1\n",
        "app/workers/runner.py": "y = 2\n",
        "frontend/src/app/home/page.tsx": "export default 1;\n",
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def make_feature(feature_id: str, entry_points: list[str], status: str = "untested") -> Feature:
    """WHAT: Builds a minimal valid Feature for coverage and rendering tests.
    WHY: Keeps each test focused on the one rule it checks.

    Args:
        feature_id: id to use.
        entry_points: the feature's entry points.
        status: status to use.
    Returns:
        A Feature with one code evidence item.
    Example:
        make_feature("f1", ["GET /x"]) -> Feature(id="f1", status="untested", ...)
    """
    evidence = [Evidence(kind="code", file="app/x.py", line=1, contains="x")]
    return Feature(feature_id, "name", "billing", entry_points, status, "", "", evidence)


def test_discovery_finds_routes_tasks_files_and_ignores_comments(tmp_path: Path) -> None:
    """WHAT: Checks discovery returns the two real routes with their full URLs (mount prefix + router
    prefix + path, including a multi-line decorator), skips the commented-out decorator, marks the
    unmounted router's route as UNRESOLVED, and finds both celery tasks and the service, worker and page.
    WHY: Missing or invented inventory items would make the coverage check meaningless.

    Args:
        tmp_path: pytest's temporary folder.
    Returns:
        Nothing.
    Example:
        @router.get('/a') mounted at /api/v1/good with router prefix /sub -> "GET /api/v1/good/sub/a"
    """
    keys = {item.key for item in discover_inventory(build_fixture_v1(tmp_path))}
    assert "GET /api/v1/good/sub/a" in keys
    assert "POST /api/v1/good/sub/b" in keys
    assert not any("commented_out" in key for key in keys)
    assert "UNRESOLVED app/api/unmounted.py::lonely" in keys
    assert {"named_task", "app.tasks.jobs.unnamed"} <= keys
    assert {"app/services/thing.py", "app/workers/runner.py", "frontend/src/app/home/page.tsx"} <= keys


def test_coverage_catches_uncovered_and_duplicated_items() -> None:
    """WHAT: Checks check_coverage reports an uncovered route, a route claimed by two features, an
    entry point that does not exist, and accepts an ignored item.
    WHY: Every discovered item must belong to exactly one feature or be explicitly ignored.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        routes A, B, C where A is in two features and C in none -> problems mention "duplicated" and "uncovered: GET /c"
    """
    inventory = [InventoryItem("GET /a", "route", "a.py"), InventoryItem("GET /b", "route", "b.py"),
                 InventoryItem("GET /c", "route", "c.py"), InventoryItem("pkg/__init__.py", "service", "pkg")]
    features = [make_feature("f1", ["GET /a", "GET /ghost"]), make_feature("f2", ["GET /a", "GET /b"])]
    problems = check_coverage(inventory, features, {"pkg/__init__.py": "package marker"})
    text = chr(10).join(problems)
    assert "uncovered: GET /c" in text
    assert "duplicated" in text and "GET /a" in text
    assert "not found in the inventory: GET /ghost" in text
    assert "pkg/__init__.py" not in text


def write_results(results_dir: Path, outcome: str) -> None:
    """WHAT: Writes a one-test results.json for a run called "run1".
    WHY: Evidence tests need a recorded test result to check against.

    Args:
        results_dir: folder that will hold run1/results.json.
        outcome: the outcome to record for test "t.py::test_x".
    Returns:
        Nothing.
    Example:
        write_results(tmp_path, "passed") -> run1/results.json with one passed row
    """
    run_dir = results_dir / "run1"
    run_dir.mkdir(parents=True)
    rows = [{"file": "t.py", "name": "test_x", "outcome": outcome, "duration_seconds": 0.0, "reason": ""}]
    (run_dir / "results.json").write_text(json.dumps({"tests": rows}), encoding="utf-8")


def test_evidence_check_lists_every_bad_citation(tmp_path: Path) -> None:
    """WHAT: Builds one feature with a wrong code line, a test id that does not exist, a test with the
    wrong expected outcome and a doc text that is absent, plus one correct code item, and checks all four
    bad citations are reported together.
    WHY: A reviewer should see every bad citation in one run, not only the first.

    Args:
        tmp_path: pytest's temporary folder.
    Returns:
        Nothing.
    Example:
        4 bad items and 1 good item -> exactly 4 problems
    """
    v1_dir = tmp_path / "v1"
    (v1_dir / "app").mkdir(parents=True)
    (v1_dir / "app/x.py").write_text("alpha\nbeta\n", encoding="utf-8")
    write_results(tmp_path / "results", "passed")
    evidence = [
        Evidence(kind="code", file="app/x.py", line=1, contains="alpha"),
        Evidence(kind="code", file="app/x.py", line=2, contains="alpha"),
        Evidence(kind="test", run="run1", test_id="t.py::test_missing", expect="passed"),
        Evidence(kind="test", run="run1", test_id="t.py::test_x", expect="failed"),
        Evidence(kind="doc", file="app/x.py", contains="gamma"),
    ]
    feature = Feature("f1", "name", "billing", ["GET /a"], "untested", "", "", evidence)
    problems = check_evidence([feature], v1_dir, tmp_path / "results")
    assert len(problems) == 4
    assert any("app/x.py:2" in problem for problem in problems)
    assert any("test_missing" in problem and "not found" in problem for problem in problems)
    assert any("expected failed" in problem for problem in problems)
    assert any("gamma" in problem for problem in problems)


def test_working_without_a_passing_test_is_rejected() -> None:
    """WHAT: Checks parse_feature rejects "working" with only a code citation, "working" without a
    mock_check, and a malformed entry, and that every message names the feature id.
    WHY: "working" must never be claimed without a passing test and a statement of what was faked.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        status working + only code evidence -> FeatureTruthError("f1: 'working' needs a test item with expect: passed")
    """
    code_item = {"type": "code", "file": "a.py", "line": 1, "contains": "x"}
    test_item = {"type": "test", "run": "r", "test_id": "t::t", "expect": "passed"}
    base = {"id": "f1", "name": "n", "area": "billing", "entry_points": ["GET /a"], "status": "working"}
    with pytest.raises(FeatureTruthError, match="f1: 'working' needs a test item"):
        parse_feature({**base, "evidence": [code_item], "mock_check": "none"})
    with pytest.raises(FeatureTruthError, match="f1: 'working' needs mock_check"):
        parse_feature({**base, "evidence": [test_item]})
    with pytest.raises(FeatureTruthError, match="f1: missing field 'name'"):
        parse_feature({"id": "f1", "area": "billing", "entry_points": [], "status": "missing", "evidence": [code_item]})
    assert parse_feature({**base, "evidence": [test_item], "mock_check": "none"}).status == "working"


def test_render_table_counts_statuses() -> None:
    """WHAT: Renders three features (2 working, 1 mocked) and checks the summary counts and that each
    feature appears under its area.
    WHY: The summary numbers must match the rows below them.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        2 working + 1 mocked -> "- working: 2" and "- mocked: 1" and "- broken: 0"
    """
    features = [make_feature("a", ["GET /a"], "working"), make_feature("b", ["GET /b"], "working"),
                make_feature("c", ["GET /c"], "mocked")]
    text = render_table(features)
    assert "- working: 2" in text and "- mocked: 1" in text and "- broken: 0" in text
    assert "## billing" in text and "| c | name | mocked |" in text


def test_finding_citation_is_checked_and_rendered(tmp_path: Path) -> None:
    """WHAT: Builds a feature with a security finding that cites a correct line and another with a wrong
    line, checks only the wrong one is reported, and checks the rendered table lists the findings in a
    "Security findings" section generated from the data.
    WHY: Findings must be as checkable as statuses, and the table must not contain hand-typed findings.

    Args:
        tmp_path: pytest's temporary folder.
    Returns:
        Nothing.
    Example:
        a finding citing app/x.py line 2 that actually has text on line 1 -> one problem; the good one is listed
    """
    v1_dir = tmp_path / "v1"
    (v1_dir / "app").mkdir(parents=True)
    (v1_dir / "app/x.py").write_text("alpha" + chr(10) + "beta" + chr(10), encoding="utf-8")
    good = Finding("security", "good finding", Evidence(kind="code", file="app/x.py", line=1, contains="alpha"))
    bad = Finding("security", "bad finding", Evidence(kind="code", file="app/x.py", line=2, contains="alpha"))
    feature = make_feature("f1", ["GET /a"])
    feature.evidence = []  # only the findings are under test here
    feature.findings = [good, bad]
    problems = check_evidence([feature], v1_dir, tmp_path / "results")
    assert len(problems) == 1 and "app/x.py:2" in problems[0]
    text = render_table([feature])
    assert "## Security findings" in text and "good finding" in text and "f1" in text
    assert "## Security findings" not in render_table([make_feature("f2", ["GET /b"])])


def test_table_starts_with_the_working_definition_and_bad_finding_names_the_feature() -> None:
    """WHAT: Checks the rendered table carries the definition line for "working", and that a finding
    without a category raises an error naming its feature id.
    WHY: Readers must know what "working" means here, and bad YAML must point at the feature to fix.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        render_table([]) contains "working = passes on SQLite in tests; v1 production uses Postgres 15"
    """
    assert "working = passes on SQLite in tests; v1 production uses Postgres 15" in render_table([])
    code_item = {"type": "code", "file": "a.py", "line": 1, "contains": "x"}
    raw = {"id": "f9", "name": "n", "area": "billing", "entry_points": ["GET /a"], "status": "untested",
           "evidence": [code_item], "findings": [{"title": "no category"}]}
    with pytest.raises(FeatureTruthError, match="f9: a finding needs"):
        parse_feature(raw)
