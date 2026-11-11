# TERM: feature-truth table - a list of every v1 feature with a status backed by checkable evidence.
# TERM: AST - the tree Python builds from source code; we read it instead of using text search.
# TERM: inventory - the machine-found list of v1 routes, celery tasks, service/worker files and pages.
# TERM: entry point - the route, task, file or page through which a feature is reached.
# TERM: evidence - a citation (a test result, a code line or a document text) that a script can re-check.
# TERM: junit row - one line of results.json in a test-results folder: file (classname), name, outcome.
"""Discover v1's features, check the hand-written feature table against the real code and test results,
and render the human-readable table. Standard library plus PyYAML only.

Paths in evidence and entry points are relative to v1's sentinel_flow_mvp folder.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

# The script is run as `python tools/phase0/feature_truth.py`, so the repo root must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.phase0.run_v1_suite import V1_DEFAULT_TAG, clone_v1, make_writable_and_retry, parse_junit  # noqa: E402

ROUTE_METHODS = ["get", "post", "put", "patch", "delete"]
STATUSES = ["working", "mocked", "broken", "missing", "untested"]
AREAS = [
    "billing",
    "stock",
    "procurement",
    "vendor",
    "ml",
    "rag",
    "health-risk",
    "crm",
    "notifications",
    "audit",
    "admin",
    "infra",
]
EVIDENCE_TYPES = ["test", "code", "doc"]
EXPECTED_OUTCOMES = ["passed", "failed", "error"]
FINDING_CATEGORIES = ["security"]
# sourced: where the v1-final files can be read online (used only to build links in the table).
V1_BLOB_URL = "https://github.com/adithya-313/prv3/blob/v1-final/sentinel_flow_mvp/"
MAIN_FILE = "app/main.py"
WORKING_DEFINITION = "working = passes on SQLite in tests; v1 production uses Postgres 15"


class FeatureTruthError(ValueError):
    """Raised when a YAML entry is malformed; the message names the feature id."""


@dataclasses.dataclass
class InventoryItem:
    """One thing the code base exposes: a route, task, service/worker file or page."""

    key: str
    kind: str
    source: str


@dataclasses.dataclass
class Evidence:
    """One citation. Which fields matter depends on kind: test, code or doc."""

    kind: str
    run: str = ""
    test_id: str = ""
    expect: str = ""
    file: str = ""
    line: int = 0
    contains: str = ""


@dataclasses.dataclass
class Finding:
    """A problem noticed in a feature's code (for example a missing tenant filter), with a code citation."""

    category: str
    title: str
    evidence: Evidence


@dataclasses.dataclass
class Feature:
    """One row of the feature-truth table."""

    id: str
    name: str
    area: str
    entry_points: list[str]
    status: str
    note: str
    mock_check: str
    evidence: list[Evidence]
    findings: list[Finding] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class RouteFacts:
    """Facts read from one parsed file: its router prefix and its decorated route functions."""

    own_prefix: str
    routes: list[tuple[str, str, str]]


def join_paths(*parts: str) -> str:
    """WHAT: Joins URL path pieces, keeping a trailing slash only when the last piece is exactly "/".
    WHY: A router prefix plus a route path must give the URL FastAPI really serves.

    Args:
        parts: path pieces such as "/api/v1", "/notifications", "/read".
    Returns:
        The joined path.
    Example:
        ("/api/v1/notifications", "/") -> "/api/v1/notifications/"; ("/api/v1/notifications", "") -> "/api/v1/notifications"
    """
    joined = ""
    for part in parts:
        joined += part
    return joined


def string_value(node: ast.AST | None) -> str | None:
    """WHAT: Returns the text of a string constant node, or None for anything else.
    WHY: Route paths and prefixes can only be trusted when they are literal strings.

    Args:
        node: an AST node or None.
    Returns:
        The string, or None.
    Example:
        the node for "/sell" -> "/sell"; the node for a variable -> None
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def route_from_decorator(decorator: ast.expr) -> tuple[str, str | None] | None:
    """WHAT: If a decorator looks like @something.get("/path") (or post/put/patch/delete), returns
    (METHOD, path); the path is None when it is not a literal string. Other decorators give None.
    WHY: Finding routes through the AST ignores commented-out decorators and handles multi-line calls.

    Args:
        decorator: a decorator expression.
    Returns:
        (METHOD, path or None), or None when it is not a route decorator.
    Example:
        @router.post("/sell") -> ("POST", "/sell"); @staticmethod -> None
    """
    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
        return None
    if decorator.func.attr not in ROUTE_METHODS:
        return None
    path_node = decorator.args[0] if decorator.args else None
    for keyword in decorator.keywords:
        if keyword.arg == "path":
            path_node = keyword.value
    return decorator.func.attr.upper(), string_value(path_node)


def read_route_facts(source: str) -> RouteFacts:
    """WHAT: Parses one Python file and returns its APIRouter prefix and every route function in it
    as (METHOD, path-or-empty, function name). A non-literal path is stored as "?" so it shows up unresolved.
    WHY: Discovery needs both the route and the router's own prefix to build the final URL.

    Args:
        source: Python source text.
    Returns:
        RouteFacts.
    Raises:
        SyntaxError: if the file does not parse.
    Example:
        a file with router = APIRouter(prefix="/procurement") and @router.get("/x") def f -> RouteFacts("/procurement", [("GET", "/x", "f")])
    """
    tree = ast.parse(source)
    own_prefix = ""
    routes: list[tuple[str, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "APIRouter":
            for keyword in node.keywords:
                if keyword.arg == "prefix":
                    own_prefix = string_value(keyword.value) or ""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                found = route_from_decorator(decorator)
                if found is not None:
                    routes.append((found[0], found[1] if found[1] is not None else "?", node.name))
    return RouteFacts(own_prefix, routes)


def module_to_file(module: str) -> str:
    """WHAT: Converts a dotted module name to a file path relative to sentinel_flow_mvp.
    WHY: main.py imports routers by module name; we need the file that defines them.

    Args:
        module: e.g. "app.api.v1.risk".
    Returns:
        e.g. "app/api/v1/risk.py".
    Example:
        "app.api.pos_router" -> "app/api/pos_router.py"
    """
    return module.replace(".", "/") + ".py"


def read_mounts(main_source: str) -> dict[str, str]:
    """WHAT: Reads app/main.py and returns, per router file, the prefix given to include_router.
    It understands `from app.api.v1 import risk` then `risk.router`, and `from x import router as name`.
    WHY: The final URL of a route is the include_router prefix plus the router's own prefix plus the path.

    Args:
        main_source: source of app/main.py.
    Returns:
        Map of router file path -> include_router prefix.
    Example:
        include_router(risk.router, prefix="/api/v1/risk") -> {"app/api/v1/risk.py": "/api/v1/risk"}
    """
    tree = ast.parse(main_source)
    file_by_name: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            for alias in node.names:
                imported_as = alias.asname or alias.name
                is_router_object = alias.name == "router"
                file_by_name[imported_as] = module_to_file(
                    node.module if is_router_object else node.module + "." + alias.name
                )
    mounts: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "include_router":
            router_file = router_file_of_call(node, file_by_name)
            if router_file is not None:
                mounts[router_file] = prefix_of_call(node)
    return mounts


def router_file_of_call(call: ast.Call, file_by_name: dict[str, str]) -> str | None:
    """WHAT: Finds which file defines the router passed to one include_router call.
    WHY: Only routers we can map to a file get a resolved URL; others stay marked as unresolved.

    Args:
        call: the include_router call node.
        file_by_name: map of imported name -> file path.
    Returns:
        The file path, or None if the argument is not a simple name or module.router.
    Example:
        include_router(router=risk.router) with "risk" -> "app/api/v1/risk.py" in the map -> "app/api/v1/risk.py"
    """
    router_node = call.args[0] if call.args else None
    for keyword in call.keywords:
        if keyword.arg == "router":
            router_node = keyword.value
    if isinstance(router_node, ast.Name):
        return file_by_name.get(router_node.id)
    if isinstance(router_node, ast.Attribute) and isinstance(router_node.value, ast.Name):
        return file_by_name.get(router_node.value.id)
    return None


def prefix_of_call(call: ast.Call) -> str:
    """WHAT: Returns the literal prefix= keyword of an include_router call, or "" when absent.
    WHY: Some routers are mounted without a prefix.

    Args:
        call: the include_router call node.
    Returns:
        The prefix text.
    Example:
        include_router(r, prefix="/api/v1") -> "/api/v1"; include_router(r) -> ""
    """
    for keyword in call.keywords:
        if keyword.arg == "prefix":
            return string_value(keyword.value) or ""
    return ""


def discover_routes(v1_dir: Path) -> list[InventoryItem]:
    """WHAT: Finds every route decorator under app/ and builds its "METHOD /full/path" key.
    A route whose router is not mounted in main.py (or has a non-literal path) is recorded as
    "UNRESOLVED <file>::<function>" instead of guessing.
    WHY: The table must account for every endpoint, and an honest "unresolved" beats a made-up URL.

    Args:
        v1_dir: the sentinel_flow_mvp folder.
    Returns:
        Inventory items of kind "route".
    Example:
        @router.post("/sell") in pos_router mounted at /api/v1/pos -> key "POST /api/v1/pos/sell"
    """
    mounts = read_mounts((v1_dir / MAIN_FILE).read_text(encoding="utf-8"))
    items: list[InventoryItem] = []
    for path in sorted((v1_dir / "app").rglob("*.py")):
        relative = path.relative_to(v1_dir).as_posix()
        facts = read_route_facts(path.read_text(encoding="utf-8"))
        for method, route_path, function_name in facts.routes:
            mounted = relative in mounts or relative == MAIN_FILE
            if route_path == "?" or not mounted:
                items.append(InventoryItem(f"UNRESOLVED {relative}::{function_name}", "route", relative))
                continue
            mount_prefix = "" if relative == MAIN_FILE else mounts[relative]
            full_path = join_paths(
                mount_prefix, "" if relative == MAIN_FILE else facts.own_prefix, route_path
            )
            items.append(InventoryItem(f"{method} {full_path}", "route", relative))
    return mark_duplicate_keys(items)


def mark_duplicate_keys(items: list[InventoryItem]) -> list[InventoryItem]:
    """WHAT: When two different routes end up with the same "METHOD /path", appends " [source file]" to
    each so they stay distinct and both must be accounted for.
    WHY: The same URL defined twice is itself a finding (only one of them can answer a request).

    Args:
        items: inventory items.
    Returns:
        Items with unique keys.
    Example:
        two "GET /api/v1/suppliers" from supplier_router.py and main.py -> "GET /api/v1/suppliers [app/api/supplier_router.py]" and "... [app/main.py]"
    """
    counts: dict[str, int] = {}
    for item in items:
        counts[item.key] = counts.get(item.key, 0) + 1
    unique: list[InventoryItem] = []
    for item in items:
        key = item.key if counts[item.key] == 1 else f"{item.key} [{item.source}]"
        unique.append(InventoryItem(key, item.kind, item.source))
    return unique


def task_name_of(decorator: ast.expr, module_path: str, function_name: str) -> str | None:
    """WHAT: If a decorator is a celery task (@x.task(...), @shared_task or @shared_task(...)), returns
    the task name: the name= keyword when given, else "<dotted module>.<function>" (celery's default).
    WHY: Celery beat and callers refer to tasks by this name.

    Args:
        decorator: a decorator expression.
        module_path: dotted module name of the file.
        function_name: decorated function name.
    Returns:
        The task name, or None when the decorator is not a task decorator.
    Example:
        @celery_app.task(name="nightly_backup_to_drive") -> "nightly_backup_to_drive"
    """
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    is_attribute_task = isinstance(target, ast.Attribute) and target.attr == "task"
    is_shared_task = isinstance(target, ast.Name) and target.id == "shared_task"
    if not (is_attribute_task or is_shared_task):
        return None
    if isinstance(decorator, ast.Call):
        for keyword in decorator.keywords:
            if keyword.arg == "name" and string_value(keyword.value):
                return string_value(keyword.value)
    return f"{module_path}.{function_name}"


def discover_tasks(v1_dir: Path) -> list[InventoryItem]:
    """WHAT: Finds every celery task decorator under app/.
    WHY: Background jobs are features too and must be accounted for.

    Args:
        v1_dir: the sentinel_flow_mvp folder.
    Returns:
        Inventory items of kind "task".
    Example:
        @celery_app.task(name="check_overdue_purchase_orders") -> key "check_overdue_purchase_orders"
    """
    items: list[InventoryItem] = []
    for path in sorted((v1_dir / "app").rglob("*.py")):
        relative = path.relative_to(v1_dir).as_posix()
        module_path = relative[: -len(".py")].replace("/", ".")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    name = task_name_of(decorator, module_path, node.name)
                    if name is not None:
                        items.append(InventoryItem(name, "task", relative))
    return items


def discover_files(v1_dir: Path) -> list[InventoryItem]:
    """WHAT: Lists service files (app/services/**), worker files (app/workers/*.py) and frontend pages.
    WHY: Services, workers and pages are features' implementation and screens.

    Args:
        v1_dir: the sentinel_flow_mvp folder.
    Returns:
        Inventory items of kind "service", "worker" or "page".
    Example:
        app/services/fefo_engine.py -> key "app/services/fefo_engine.py"
    """
    patterns = [
        ("service", "app/services", "*.py"),
        ("worker", "app/workers", "*.py"),
        ("page", "frontend/src/app", "page.tsx"),
    ]
    items: list[InventoryItem] = []
    for kind, folder, pattern in patterns:
        for path in sorted((v1_dir / folder).rglob(pattern)):
            relative = path.relative_to(v1_dir).as_posix()
            items.append(InventoryItem(relative, kind, relative))
    return items


def discover_inventory(v1_dir: Path) -> list[InventoryItem]:
    """WHAT: Combines route, task, file and page discovery into one list.
    WHY: One list is what check_coverage compares against the hand-written table.

    Args:
        v1_dir: the sentinel_flow_mvp folder.
    Returns:
        All inventory items (a task and its file may both appear; they have different keys).
    Example:
        v1 at v1-final -> routes + tasks + 26+ service files + worker files + 8 pages
    """
    return discover_routes(v1_dir) + discover_tasks(v1_dir) + discover_files(v1_dir)


def parse_evidence(raw: object, feature_id: str) -> Evidence:
    """WHAT: Validates one evidence mapping from the YAML and turns it into an Evidence record.
    WHY: A malformed entry must stop the run with the feature id in the message.

    Args:
        raw: the YAML value.
        feature_id: id of the owning feature, for error messages.
    Returns:
        Evidence.
    Raises:
        FeatureTruthError: for a wrong type, missing field or bad value.
    Example:
        {"type": "code", "file": "app/x.py", "line": 3, "contains": "foo"} -> Evidence(kind="code", ...)
    """
    if not isinstance(raw, dict) or raw.get("type") not in EVIDENCE_TYPES:
        raise FeatureTruthError(f"{feature_id}: evidence item needs a type in {EVIDENCE_TYPES}: {raw!r}")
    required = {
        "test": ["run", "test_id", "expect"],
        "code": ["file", "line", "contains"],
        "doc": ["file", "contains"],
    }[raw["type"]]
    for field_name in required:
        if field_name not in raw:
            raise FeatureTruthError(f"{feature_id}: {raw['type']} evidence lacks '{field_name}'")
    if raw["type"] == "test" and raw["expect"] not in EXPECTED_OUTCOMES:
        raise FeatureTruthError(f"{feature_id}: expect must be one of {EXPECTED_OUTCOMES}")
    if raw["type"] == "code" and not isinstance(raw["line"], int):
        raise FeatureTruthError(f"{feature_id}: line must be a whole number")
    return Evidence(
        raw["type"],
        raw.get("run", ""),
        raw.get("test_id", ""),
        raw.get("expect", ""),
        raw.get("file", ""),
        raw.get("line", 0),
        raw.get("contains", ""),
    )


def parse_finding(raw: object, feature_id: str) -> Finding:
    """WHAT: Validates one finding mapping (category, title, file, line, contains) from the YAML.
    The citation is a code citation, checked later like any other evidence.
    WHY: Findings (such as a missing tenant filter) must be as checkable as statuses.

    Args:
        raw: the YAML value.
        feature_id: owning feature, for error messages.
    Returns:
        Finding.
    Raises:
        FeatureTruthError: for a wrong type, missing field or unknown category.
    Example:
        {"category": "security", "title": "no tenant filter", "file": "a.py", "line": 3, "contains": "x"} -> Finding(...)
    """
    if not isinstance(raw, dict) or raw.get("category") not in FINDING_CATEGORIES or not raw.get("title"):
        raise FeatureTruthError(
            f"{feature_id}: a finding needs a category in {FINDING_CATEGORIES} and a title: {raw!r}"
        )
    code_fields = {
        "type": "code",
        "file": raw.get("file"),
        "line": raw.get("line"),
        "contains": raw.get("contains"),
    }
    return Finding(raw["category"], raw["title"], parse_evidence(code_fields, feature_id))


def parse_feature(raw: object) -> Feature:
    """WHAT: Validates one feature mapping and builds a Feature, enforcing the status rules:
    every status needs evidence; "working" needs a passing test item and a mock_check.
    WHY: The table is only worth something if its rules are enforced.

    Args:
        raw: the YAML value for one feature.
    Returns:
        Feature.
    Raises:
        FeatureTruthError: naming the feature id (or the entry position if it has no id).
    Example:
        status "working" with only a code citation -> FeatureTruthError("<id>: 'working' needs ...")
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
        raise FeatureTruthError(f"feature entry without a text id: {raw!r}")
    feature_id = raw["id"]
    for field_name in ["name", "area", "entry_points", "status", "evidence"]:
        if field_name not in raw:
            raise FeatureTruthError(f"{feature_id}: missing field '{field_name}'")
    if raw["status"] not in STATUSES or raw["area"] not in AREAS:
        raise FeatureTruthError(f"{feature_id}: status must be in {STATUSES} and area in {AREAS}")
    evidence = [parse_evidence(item, feature_id) for item in raw["evidence"] or []]
    findings = [parse_finding(item, feature_id) for item in raw.get("findings") or []]
    feature = Feature(
        feature_id,
        raw["name"],
        raw["area"],
        list(raw["entry_points"] or []),
        raw["status"],
        raw.get("note", ""),
        raw.get("mock_check", ""),
        evidence,
        findings,
    )
    validate_status_rules(feature)
    return feature


def validate_status_rules(feature: Feature) -> None:
    """WHAT: Applies the rules that tie a status to its evidence.
    WHY: "working" must never be claimed without a passing, un-mocked test.

    Args:
        feature: a parsed Feature.
    Returns:
        Nothing.
    Raises:
        FeatureTruthError: if a rule is broken.
    Example:
        working + no mock_check -> FeatureTruthError("<id>: 'working' needs mock_check ...")
    """
    if not feature.evidence:
        raise FeatureTruthError(f"{feature.id}: every status needs at least one evidence item")
    if feature.status == "working":
        passing_tests = [item for item in feature.evidence if item.kind == "test" and item.expect == "passed"]
        if not passing_tests:
            raise FeatureTruthError(f"{feature.id}: 'working' needs a test item with expect: passed")
        if not feature.mock_check:
            raise FeatureTruthError(
                f"{feature.id}: 'working' needs mock_check (file::name of the mocks, or 'none')"
            )
    if not feature.entry_points and feature.status != "missing":
        raise FeatureTruthError(f"{feature.id}: only 'missing' features may have no entry_points")


def load_features(path: Path) -> list[Feature]:
    """WHAT: Reads features.yaml and validates every entry.
    WHY: Bad input stops here, with the offending feature id.

    Args:
        path: the YAML file (a list of feature mappings under the key "features").
    Returns:
        The features.
    Raises:
        FeatureTruthError: for a malformed entry or duplicate ids.
    Example:
        a file with 2 valid entries -> 2 Feature objects
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("features"), list):
        raise FeatureTruthError(f"{path}: expected a mapping with a 'features' list")
    features = [parse_feature(raw) for raw in document["features"]]
    seen: set[str] = set()
    for feature in features:
        if feature.id in seen:
            raise FeatureTruthError(f"{feature.id}: duplicate feature id")
        seen.add(feature.id)
    return features


def load_ignored(path: Path) -> dict[str, str]:
    """WHAT: Reads ignored.yaml: inventory keys that are not features, each with a reason.
    WHY: Every discovered item must be either a feature entry point or explicitly ignored.

    Args:
        path: the YAML file (mapping "ignored" with a list of {key, reason}).
    Returns:
        Map of key -> reason.
    Raises:
        FeatureTruthError: for a missing key or reason.
    Example:
        [{"key": "app/services/__init__.py", "reason": "package marker"}] -> {"app/services/__init__.py": "package marker"}
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = document.get("ignored") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        raise FeatureTruthError(f"{path}: expected a mapping with an 'ignored' list")
    ignored: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("key") or not entry.get("reason"):
            raise FeatureTruthError(f"{path}: every ignored entry needs a key and a reason: {entry!r}")
        ignored[entry["key"]] = entry["reason"]
    return ignored


def check_coverage(
    inventory: list[InventoryItem], features: list[Feature], ignored: dict[str, str]
) -> list[str]:
    """WHAT: Checks that every discovered item appears in exactly one feature's entry_points or in the
    ignored list, and that no entry point names something that was not discovered.
    WHY: This stops features from being silently forgotten or counted twice.

    Args:
        inventory: discovered items.
        features: validated features.
        ignored: key -> reason.
    Returns:
        A list of problems (empty when everything is covered exactly once).
    Example:
        a route in no feature and not ignored -> ["uncovered: GET /api/v1/x"]
    """
    owners: dict[str, list[str]] = {}
    for feature in features:
        for entry_point in feature.entry_points:
            owners.setdefault(entry_point, []).append(feature.id)
    problems: list[str] = []
    discovered = {item.key for item in inventory}
    for key in sorted(discovered):
        owner_ids = owners.get(key, [])
        if key in ignored and owner_ids:
            problems.append(f"both ignored and owned by {owner_ids}: {key}")
        elif key not in ignored and not owner_ids:
            problems.append(f"uncovered: {key}")
        elif len(owner_ids) > 1:
            problems.append(f"duplicated in {owner_ids}: {key}")
    for key in sorted(set(owners) - discovered):
        problems.append(f"entry point not found in the inventory: {key} (feature {owners[key]})")
    for key in sorted(set(ignored) - discovered):
        problems.append(f"ignored key not found in the inventory: {key}")
    return problems


def load_test_outcomes(results_dir: Path, run: str) -> dict[str, set[str]]:
    """WHAT: Reads <results_dir>/<run>/results.json into a map "file::name" -> set of outcomes. If that
    folder has no results.json but has a junit.xml (the scorecard flaw run), the junit XML is parsed instead
    with the same reconciling parser. A test with two results (e.g. failed plus a teardown error) has both
    outcomes in its set.
    WHY: Test evidence is checked against the recorded results, never against memory.

    Args:
        results_dir: folder holding one sub-folder per run.
        run: sub-folder name, e.g. "docker-with-db".
    Returns:
        The map; empty if the run has neither results.json nor junit.xml.
    Example:
        a row (file "test_a", name "test_x", outcome "passed") -> {"test_a::test_x": {"passed"}}
    """
    results_path = results_dir / run / "results.json"
    junit_path = results_dir / run / "junit.xml"
    if results_path.exists():
        rows = json.loads(results_path.read_text(encoding="utf-8"))["tests"]
    elif junit_path.exists():
        rows = [dataclasses.asdict(row) for row in parse_junit(junit_path)]
    else:
        return {}
    outcomes: dict[str, set[str]] = {}
    for row in rows:
        outcomes.setdefault(f"{row['file']}::{row['name']}", set()).add(row["outcome"])
    return outcomes


def check_one_evidence(
    feature_id: str,
    item: Evidence,
    v1_dir: Path,
    results_dir: Path,
    outcomes_by_run: dict[str, dict[str, set[str]]],
) -> str | None:
    """WHAT: Checks a single evidence item and returns a problem text, or None when it holds.
    test: the id exists in that run with the expected outcome ("passed" means every result passed).
    code: the file exists and the given line contains the text. doc: the file contains the text.
    WHY: Citations must be re-checkable by a script.

    Args:
        feature_id: owning feature, for the message.
        item: the evidence.
        v1_dir: the sentinel_flow_mvp folder.
        results_dir: folder of test-result runs.
        outcomes_by_run: cache of loaded runs (filled here).
    Returns:
        A problem string or None.
    Example:
        code evidence with the wrong line number -> "f1: app/x.py:9 does not contain 'foo'"
    """
    if item.kind == "test":
        if item.run not in outcomes_by_run:
            outcomes_by_run[item.run] = load_test_outcomes(results_dir, item.run)
        outcomes = outcomes_by_run[item.run].get(item.test_id)
        if outcomes is None:
            return f"{feature_id}: test {item.test_id} not found in run {item.run}"
        matches = outcomes == {"passed"} if item.expect == "passed" else item.expect in outcomes
        return (
            None
            if matches
            else f"{feature_id}: test {item.test_id} has outcomes {sorted(outcomes)}, expected {item.expect}"
        )
    path = v1_dir / item.file
    if not path.is_file():
        return f"{feature_id}: file not found: {item.file}"
    text = path.read_text(encoding="utf-8", errors="replace")
    if item.kind == "doc":
        return (
            None if item.contains in text else f"{feature_id}: {item.file} does not contain {item.contains!r}"
        )
    lines = text.splitlines()
    if not 1 <= item.line <= len(lines) or item.contains not in lines[item.line - 1]:
        return f"{feature_id}: {item.file}:{item.line} does not contain {item.contains!r}"
    return None


def check_evidence(features: list[Feature], v1_dir: Path, results_dir: Path) -> list[str]:
    """WHAT: Checks every evidence item of every feature and returns ALL problems, not just the first.
    WHY: A reviewer should fix every bad citation in one pass.

    Args:
        features: validated features.
        v1_dir: the sentinel_flow_mvp folder.
        results_dir: folder of test-result runs.
    Returns:
        A list of problems (empty when all citations hold).
    Example:
        one wrong line and one missing test id -> a list of 2 strings
    """
    outcomes_by_run: dict[str, dict[str, set[str]]] = {}
    problems: list[str] = []
    for feature in features:
        cited = feature.evidence + [finding.evidence for finding in feature.findings]
        for item in cited:
            problem = check_one_evidence(feature.id, item, v1_dir, results_dir, outcomes_by_run)
            if problem is not None:
                problems.append(problem)
    return problems


def evidence_link(item: Evidence) -> str:
    """WHAT: Builds a Markdown link for one evidence item: code/doc go to the v1-final file on GitHub,
    tests go to the run's results.md in this repo.
    WHY: A reader of the table can open the evidence in one click.

    Args:
        item: the evidence.
    Returns:
        Markdown text.
    Example:
        code evidence app/x.py line 9 -> "[app/x.py:9](https://github.com/.../app/x.py#L9)"
    """
    if item.kind == "test":
        return f"[{item.test_id} ({item.expect})](../v1-test-results/{item.run}/results.md)"
    if item.kind == "code":
        return f"[{item.file}:{item.line}]({V1_BLOB_URL}{item.file}#L{item.line})"
    return f"[{item.file}]({V1_BLOB_URL}{item.file})"


def render_findings(features: list[Feature]) -> list[str]:
    """WHAT: Builds one "<Category> findings" section per finding category, generated from the findings
    field of features.yaml: feature id, title and a link to the cited line.
    WHY: Findings must come from the data, never be typed into the rendered table.

    Args:
        features: validated features.
    Returns:
        Markdown lines (empty when no feature has findings).
    Example:
        one security finding on feature "inventory-triage" -> ["", "## Security findings", "", "- **inventory-triage**: ..."]
    """
    lines: list[str] = []
    for category in FINDING_CATEGORIES:
        entries = [
            (feature, finding)
            for feature in features
            for finding in feature.findings
            if finding.category == category
        ]
        if not entries:
            continue
        lines += ["", f"## {category.capitalize()} findings", ""]
        for feature, finding in entries:
            lines.append(f"- **{feature.id}**: {finding.title} ({evidence_link(finding.evidence)})")
    return lines


def render_table(features: list[Feature]) -> str:
    """WHAT: Renders the Markdown table: a count per status, then one table per area with links.
    WHY: This is the human-readable output; it is generated, never edited by hand.

    Args:
        features: validated features.
    Returns:
        The Markdown text.
    Example:
        2 working + 1 mocked -> a summary line "working: 2, mocked: 1, ..." and the rows below it
    """
    lines = [
        "# v1 feature-truth table",
        "",
        "_Generated by tools/phase0/feature_truth.py. Do not edit by hand._",
        "",
        WORKING_DEFINITION,
        "",
        "## Summary",
        "",
    ]
    for status in STATUSES:
        lines.append(f"- {status}: {sum(1 for feature in features if feature.status == status)}")
    for area in AREAS:
        in_area = [feature for feature in features if feature.area == area]
        if not in_area:
            continue
        lines += [
            "",
            f"## {area}",
            "",
            "| id | feature | status | note | evidence |",
            "|---|---|---|---|---|",
        ]
        for feature in in_area:
            links = "<br>".join(evidence_link(item) for item in feature.evidence)
            note = feature.note.replace("|", "/").replace(chr(10), " ")
            lines.append(f"| {feature.id} | {feature.name} | {feature.status} | {note} | {links} |")
    lines += render_findings(features)
    return chr(10).join(lines) + chr(10)


def run_checks(
    v1_dir: Path, features_path: Path, ignored_path: Path, results_dir: Path
) -> tuple[list[Feature], list[str]]:
    """WHAT: Loads the YAML, discovers the inventory, and runs both checks.
    WHY: Keeps main() short and testable.

    Args:
        v1_dir: the sentinel_flow_mvp folder.
        features_path: features.yaml.
        ignored_path: ignored.yaml.
        results_dir: folder of test-result runs.
    Returns:
        (features, problems from both checks).
    Raises:
        FeatureTruthError: for malformed YAML.
    Example:
        a consistent table -> (features, [])
    """
    features = load_features(features_path)
    ignored = load_ignored(ignored_path)
    inventory = discover_inventory(v1_dir)
    problems = check_coverage(inventory, features, ignored) + check_evidence(features, v1_dir, results_dir)
    return features, problems


def main(argv: list[str]) -> int:
    """WHAT: Clones v1-final fresh into a temp folder, runs both checks, renders the table, and returns
    non-zero if any check failed. The temp folder is always removed.
    WHY: Re-checking against a fresh clone proves the evidence is about the tagged code.

    Args:
        argv: command-line arguments.
    Returns:
        0 when everything holds, 1 when there are problems.
    Side effects:
        Writes feature-truth.md into --out (default: --dir), only when all checks pass.
    Example:
        main([]) with a consistent table -> prints "OK: 57 features ..." and returns 0
    """
    parser = argparse.ArgumentParser(description="Check and render the v1 feature-truth table.")
    parser.add_argument("--tag", default=V1_DEFAULT_TAG)
    parser.add_argument("--dir", default="docs/phase0/feature-truth")
    parser.add_argument("--results-dir", default="docs/phase0/v1-test-results")
    parser.add_argument(
        "--out",
        default=None,
        help="folder to render into (default: the same as --dir); inputs are always read from --dir",
    )
    args = parser.parse_args(argv)
    work_root = Path(tempfile.mkdtemp(prefix="feature_truth_"))
    try:
        v1_dir = clone_v1(args.tag, work_root / "v1")
        directory = Path(args.dir)
        features, problems = run_checks(
            v1_dir, directory / "features.yaml", directory / "ignored.yaml", Path(args.results_dir)
        )
        for problem in problems:
            print("PROBLEM:", problem)
        if problems:
            return 1
        out_directory = directory
        if args.out:
            out_directory = Path(args.out)
        out_directory.mkdir(parents=True, exist_ok=True)
        (out_directory / "feature-truth.md").write_text(render_table(features), encoding="utf-8")
        print(f"OK: {len(features)} features, all items covered, all evidence verified")
        return 0
    finally:
        shutil.rmtree(work_root, onerror=make_writable_and_retry)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
