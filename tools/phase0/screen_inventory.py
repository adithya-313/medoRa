# TERM: screen inventory - a list of what each v1 screen DOES (actions, data, API calls), not how it looks.
# TERM: App Router - Next.js folder rule: a folder src/app/foo/page.tsx is served at the route /foo.
# TERM: route group - a Next.js folder named in parentheses, e.g. (admin); it is not part of the URL.
# TERM: dead call - a frontend call to an /api/v1 path for which no backend route exists.
# TERM: orphan file - a source file that no other frontend file imports, so no screen can reach it.
# TERM: must_keep - a v2 requirement: a user action whose feature is working, broken or untested in v1.
"""Read v1's frontend code and record, with checkable evidence, what each screen does so v2 loses no useful
function. The look of v1 is deliberately NOT recorded (v2 gets a new design).

This is a TEXT SCAN of TypeScript, not a parser. It reads lines; it cannot follow variables or imports
across files except for the simple import check used for orphan files.
"""
from __future__ import annotations

import argparse
import dataclasses
import re
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

# The script is run as `python tools/phase0/screen_inventory.py`, so the repo root must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.phase0.data_provenance import clone_exact  # noqa: E402
from tools.phase0.feature_truth import Evidence, FeatureTruthError, check_one_evidence, discover_inventory, load_features, parse_evidence  # noqa: E402
from tools.phase0.run_v1_suite import V1_DEFAULT_TAG, make_writable_and_retry  # noqa: E402

FRONTEND = "frontend"
SOURCE_SUFFIXES = [".ts", ".tsx"]
HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
METHOD_NOT_DETERMINED = "method not determined"
MUST_KEEP_STATUSES = ["working", "broken", "untested"]
REBUILD_STATUSES = ["mocked", "missing"]
DATA_SOURCES = ["api", "hard-coded", "local-state"]
# must_keep / rebuild_in_v2 entries may name these instead of a page route (no single screen owns the action).
NON_PAGE_SCOPES = ["none", "all pages"]
# policy: a call expression spanning more than this many lines is not followed (reported as undetermined).
MAX_CALL_LINES = 15
# Matches a quote or backtick followed by an /api/v1 path up to the closing quote, backtick or whitespace,
# e.g. `'/api/v1/pos/checkout'` or "`/api/v1/suppliers/${id}/orders`". It does NOT match a path split over
# several string pieces or built from variables ("/api/" + version).
API_PATH_PATTERN = re.compile(r"[`'\"](/api/v1[^`'\"\s]*)")
# Matches a ${...} placeholder inside a template literal, e.g. ${supplierId}. Nested braces are not handled.
TEMPLATE_PARAMETER_PATTERN = re.compile(r"\$\{[^}]*\}")
# Matches a backend path parameter such as {po_id}.
BACKEND_PARAMETER_PATTERN = re.compile(r"\{[^}/]+\}")
# Matches the start of a network call: fetch( , api.get( , api.post( , request( ... It does NOT match calls made
# through other helper names.
CALL_START_PATTERN = re.compile(r"\b(fetch|api\.(?:get|post|put|patch|delete)|request)\(")
# Matches an explicit HTTP method option such as method: 'POST'.
METHOD_OPTION_PATTERN = re.compile(r"method\s*:\s*['\"]([A-Za-z]+)['\"]")
# Matches the first argument of request('GET', ...) used in api.ts.
REQUEST_METHOD_PATTERN = re.compile(r"\brequest\(\s*['\"]([A-Za-z]+)['\"]")
# Matches a Next.js route-group folder name such as (admin).
ROUTE_GROUP_PATTERN = re.compile(r"^\(.*\)$")


class ScreenInventoryError(ValueError):
    """Raised when a screens.yaml entry is malformed; the message names the page, call or action."""


@dataclasses.dataclass
class Page:
    """One page.tsx and the URL route it serves."""

    route: str
    file: str


@dataclasses.dataclass
class FrontendCall:
    """One /api/v1 path string found in frontend code."""

    path: str
    raw: str
    file: str
    line: int
    method: str


@dataclasses.dataclass
class CallMatch:
    """The result of matching one frontend call against the backend routes."""

    call: FrontendCall
    status: str
    backend_routes: list[str]


@dataclasses.dataclass
class UnusedEvidence:
    """A checkable negative claim: an identifier used nowhere else, or a file imported by no one."""

    kind: str
    name: str
    defined_in: str


def normalise_frontend_path(raw: str) -> str:
    """WHAT: Turns a path found in frontend code into a comparable form: query string removed, every ${...}
    replaced by {param}, trailing slash removed.
    WHY: `/api/v1/audit-logs?${params}` and `/api/v1/suppliers/${id}/orders` must compare with backend routes.

    Args:
        raw: the text after the opening quote, e.g. "/api/v1/suppliers/${supplierId}/orders".
    Returns:
        The normalised path.
    Example:
        "/api/v1/health-risk/history?days=${days}" -> "/api/v1/health-risk/history"
    """
    without_query = raw.split("?", 1)[0]
    with_params = TEMPLATE_PARAMETER_PATTERN.sub("{param}", without_query)
    return with_params.rstrip("/") if len(with_params) > 1 else with_params


def normalise_backend_path(path: str) -> str:
    """WHAT: Turns a backend route path into the same comparable form: {anything} becomes {param}.
    WHY: Backend uses {po_id}, {supplier_id}, ...; the frontend scan only knows {param}.

    Args:
        path: a backend path such as "/api/v1/purchase-orders/{po_id}".
    Returns:
        The normalised path.
    Example:
        "/api/v1/suppliers/{supplier_id}/orders" -> "/api/v1/suppliers/{param}/orders"
    """
    normalised = BACKEND_PARAMETER_PATTERN.sub("{param}", path)
    return normalised.rstrip("/") if len(normalised) > 1 else normalised


def page_route(relative_page_path: str) -> tuple[str, bool]:
    """WHAT: Maps a page.tsx path inside src/app to its URL route using the App Router folder rule: the folder
    path is the route, route groups "(name)" are dropped, the root page is "/".
    WHY: Screens are identified by their route.

    Args:
        relative_page_path: path relative to src/app, e.g. "admin/settings/page.tsx".
    Returns:
        (route, True if any folder was a route group).
    Limitations:
        Parallel routes (@slot) and intercepting routes are not handled; v1 has none.
    Example:
        "admin/settings/page.tsx" -> ("/admin/settings", False); "page.tsx" -> ("/", False)
    """
    folders = relative_page_path.split("/")[:-1]
    kept = [folder for folder in folders if not ROUTE_GROUP_PATTERN.match(folder)]
    return "/" + "/".join(kept), len(kept) != len(folders)


def discover_pages(v1_dir: Path) -> tuple[list[Page], bool]:
    """WHAT: Finds every page.tsx under frontend/src/app and the route it serves.
    WHY: Every page must have an entry in screens.yaml.

    Args:
        v1_dir: the sentinel_flow_mvp folder.
    Returns:
        (pages sorted by route, True if any route group folder exists).
    Example:
        v1-final -> 8 pages, from "/" to "/suppliers", and False (no route groups)
    """
    app_dir = v1_dir / FRONTEND / "src" / "app"
    pages: list[Page] = []
    any_group = False
    for path in sorted(app_dir.rglob("page.tsx")):
        route, has_group = page_route(path.relative_to(app_dir).as_posix())
        any_group = any_group or has_group
        pages.append(Page(route, path.relative_to(v1_dir).as_posix()))
    return sorted(pages, key=lambda page: page.route), any_group


def source_files(v1_dir: Path) -> list[Path]:
    """WHAT: Lists the .ts/.tsx files of the frontend that the scans read: frontend/src and frontend/components
    (the older top-level components folder), excluding node_modules.
    WHY: One definition of "frontend code" for every scan.

    Args:
        v1_dir: the sentinel_flow_mvp folder.
    Returns:
        Sorted file paths.
    Example:
        v1-final -> 41 files under frontend/src plus 8 under frontend/components
    """
    files: list[Path] = []
    for folder in ("src", "components"):
        for suffix in SOURCE_SUFFIXES:
            files += [path for path in (v1_dir / FRONTEND / folder).rglob(f"*{suffix}") if "node_modules" not in path.parts]
    return sorted(files)


def call_text(lines: list[str], start_index: int) -> str:
    """WHAT: Returns the text of the call that starts on lines[start_index]: from the call start until its
    parentheses balance (at most MAX_CALL_LINES lines). Parentheses inside strings are counted, which is
    good enough for the simple calls in v1.
    WHY: A fetch options object often spans several lines, and the HTTP method lives inside it.

    Args:
        lines: all lines of the file.
        start_index: zero-based index of the line where the call starts.
    Returns:
        The joined text of the call.
    Example:
        "fetch(url, {" + "method: 'POST'," + "})" on 3 lines -> the three lines joined
    """
    depth = 0
    collected: list[str] = []
    for line in lines[start_index:start_index + MAX_CALL_LINES]:
        collected.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            break
    return " ".join(collected)


def method_of_call(lines: list[str], index: int) -> str:
    """WHAT: Reads the HTTP method of the network call on or just above lines[index].
    api.get/post/... and request('GET', ...) name it directly; fetch(...) uses its method option, or GET when
    the whole call was read and has no method option.
    WHY: Frontend and backend methods are compared where the method is readable.

    Args:
        lines: all lines of the file.
        index: zero-based line index of the path string.
    Returns:
        "GET", "POST", ... or METHOD_NOT_DETERMINED.
    Limitations:
        If the path string is on a different line from the call start by more than one line, it is not found.
    Example:
        a line "const r = await api.post('/api/v1/suppliers', {" -> "POST"
    """
    for back in (0, 1):
        if index - back < 0:
            continue
        line = lines[index - back]
        request_match = REQUEST_METHOD_PATTERN.search(line)
        if request_match and request_match.group(1).upper() in HTTP_METHODS:
            return request_match.group(1).upper()
        start = CALL_START_PATTERN.search(line)
        if start is None:
            continue
        kind = start.group(1)
        if kind.startswith("api."):
            return kind.split(".")[1].upper()
        if kind == "fetch":
            option = METHOD_OPTION_PATTERN.search(call_text(lines, index - back))
            return option.group(1).upper() if option else "GET"
    return METHOD_NOT_DETERMINED


def is_comment_line(line: str) -> bool:
    """WHAT: True when the whole line is a comment (starts with //, /* or *).
    WHY: URLs inside comments are not calls.

    Args:
        line: one source line.
    Returns:
        True for comment lines.
    Example:
        "  // see /api/v1/foo" -> True; "fetch('/api/v1/foo')" -> False
    """
    stripped = line.strip()
    return stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*")


def discover_frontend_calls(v1_dir: Path) -> list[FrontendCall]:
    """WHAT: Finds every quoted or template-literal "/api/v1/..." string in the frontend .ts/.tsx files and
    records its normalised path, file, line and HTTP method (if readable).
    WHY: The frontend's real API usage must be compared with the backend's routes.

    Args:
        v1_dir: the sentinel_flow_mvp folder.
    Returns:
        FrontendCall list in file and line order.
    Limitations:
        A text scan: it misses URLs built from variables or split over several strings, and counts a path in
        a string even if the code around it never runs. Non-literal call URLs are reported separately.
    Example:
        api.post('/api/v1/suppliers', {...}) at AddVendorModal.tsx line 31 -> FrontendCall("/api/v1/suppliers", ..., 31, "POST")
    """
    calls: list[FrontendCall] = []
    for path in source_files(v1_dir):
        lines = path.read_text(encoding="utf-8").splitlines()
        relative = path.relative_to(v1_dir).as_posix()
        for index, line in enumerate(lines):
            if is_comment_line(line):
                continue
            for match in API_PATH_PATTERN.finditer(line):
                calls.append(FrontendCall(normalise_frontend_path(match.group(1)), match.group(1), relative,
                                          index + 1, method_of_call(lines, index)))
    return calls


def discover_non_literal_calls(v1_dir: Path) -> list[tuple[str, int, str]]:
    """WHAT: Finds fetch/api.get/api.post/request calls whose first argument is not a quoted or template string
    (a variable, for example), so a reviewer can check by hand where they lead.
    WHY: The literal scan cannot see these URLs.

    Args:
        v1_dir: the sentinel_flow_mvp folder.
    Returns:
        (file, line, line text) triples.
    Limitations:
        Only the first argument on the same line as the call start is examined.
    Example:
        "const res = await fetch(url, { headers })" at api.ts line 204 -> ("frontend/src/services/api.ts", 204, "...")
    """
    found: list[tuple[str, int, str]] = []
    for path in source_files(v1_dir):
        relative = path.relative_to(v1_dir).as_posix()
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            match = CALL_START_PATTERN.search(line)
            if match is None or is_comment_line(line):
                continue
            argument = line[match.end():].lstrip()
            if argument[:1] not in ("'", '"', "`") and not argument.startswith("method"):
                found.append((relative, index + 1, line.strip()))
    return found


def split_route_key(route_key: str) -> tuple[str, str] | None:
    """WHAT: Splits a feature_truth route key "GET /api/v1/x [file]" into (METHOD, normalised path).
    WHY: Backend routes come from feature_truth's discovery in that format.

    Args:
        route_key: an inventory key.
    Returns:
        (method, path), or None for keys that are not routes (files, tasks, UNRESOLVED ...).
    Example:
        "GET /api/v1/suppliers [app/main.py]" -> ("GET", "/api/v1/suppliers")
    """
    parts = route_key.split(" ", 2)
    if len(parts) < 2 or parts[0] not in HTTP_METHODS or not parts[1].startswith("/"):
        return None
    return parts[0], normalise_backend_path(parts[1])


def path_matches(call_path: str, route_path: str) -> int:
    """WHAT: Compares a normalised call path with a normalised backend path segment by segment.
    A {param} on either side matches any segment; otherwise segments must be equal.
    WHY: `/api/v1/purchase-orders/receive` must prefer the literal route over `/purchase-orders/{param}`.

    Args:
        call_path: normalised frontend path.
        route_path: normalised backend path.
    Returns:
        -1 if they do not match, otherwise the number of literally equal segments (higher is better).
    Example:
        ("/api/v1/a/{param}", "/api/v1/a/{param}") -> 3; ("/api/v1/a/x", "/api/v1/b/x") -> -1
    """
    call_segments = call_path.split("/")
    route_segments = route_path.split("/")
    if len(call_segments) != len(route_segments):
        return -1
    literal_matches = 0
    for call_segment, route_segment in zip(call_segments, route_segments):
        if call_segment == route_segment:
            literal_matches += 1
        elif "{param}" not in (call_segment, route_segment):
            return -1
    return literal_matches


def match_calls_to_routes(calls: list[FrontendCall], backend_routes: list[str]) -> list[CallMatch]:
    """WHAT: Matches each frontend call to the best backend route path and compares HTTP methods.
    Statuses: "matched", "method mismatch", "method not determined" (path matched, call method unreadable),
    "dead" (no backend path matches).
    WHY: A dead call is a frontend feature with no server behind it; a mismatch is a latent bug.

    Args:
        calls: discovered frontend calls.
        backend_routes: inventory route keys (from feature_truth.discover_inventory).
    Returns:
        One CallMatch per call.
    Example:
        a POST to /api/v1/suppliers with backend routes GET and POST /api/v1/suppliers -> "matched"
    """
    routes = [parts for parts in (split_route_key(key) for key in backend_routes) if parts is not None]
    matches: list[CallMatch] = []
    for call in calls:
        scored = [(path_matches(call.path, path), method, path) for method, path in routes]
        best_score = max((score for score, _, _ in scored), default=-1)
        if best_score < 0:
            matches.append(CallMatch(call, "dead", []))
            continue
        best = [(method, path) for score, method, path in scored if score == best_score]
        methods = sorted({method for method, _ in best})
        keys = [f"{method} {path}" for method, path in best]
        if call.method == METHOD_NOT_DETERMINED:
            matches.append(CallMatch(call, "method not determined", keys))
        elif call.method in methods:
            matches.append(CallMatch(call, "matched", keys))
        else:
            matches.append(CallMatch(call, "method mismatch", keys))
    return matches


def import_pattern(file_path: Path) -> re.Pattern[str]:
    """WHAT: Builds a regex that finds an import of the given source file by its module name, e.g. an import
    ending in "/TelemetryEngine" or "./TelemetryEngine".
    WHY: Used to prove a file is an orphan (imported by nobody).

    Args:
        file_path: the source file.
    Returns:
        A compiled regex matching `from '...'` or `import '...'` lines that end with the module name.
    Limitations:
        Dynamic imports and re-exports through index files are not followed.
    Example:
        Path("components/TelemetryEngine.tsx") -> matches "import X from '@components/TelemetryEngine'"
    """
    module = re.escape(file_path.stem)
    return re.compile(rf"(?:from|import)\s*\(?\s*['\"][^'\"]*[/.]{module}['\"]")


def is_imported_anywhere(v1_dir: Path, target: Path) -> bool:
    """WHAT: True if any other frontend source file has an import that ends with the target's module name.
    WHY: Proves or refutes "orphan file".

    Args:
        v1_dir: the sentinel_flow_mvp folder.
        target: the file under test.
    Returns:
        True when some other file imports it.
    Example:
        components/TelemetryEngine.tsx, imported by nobody -> False
    """
    pattern = import_pattern(target)
    for path in source_files(v1_dir):
        if path != target and pattern.search(path.read_text(encoding="utf-8")):
            return True
    return False


def is_identifier_used_elsewhere(v1_dir: Path, name: str, defined_in: Path) -> bool:
    """WHAT: True if the identifier appears as a whole word in any frontend source file other than the one
    that defines it.
    WHY: Proves or refutes "this exported function is never used".

    Args:
        v1_dir: the sentinel_flow_mvp folder.
        name: identifier, e.g. "listSuppliers".
        defined_in: the file that defines it.
    Returns:
        True when some other file mentions it.
    Example:
        listSuppliers (defined in api.ts, mentioned nowhere else) -> False
    """
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    for path in source_files(v1_dir):
        if path != defined_in and pattern.search(path.read_text(encoding="utf-8")):
            return True
    return False


def check_unused_evidence(item: UnusedEvidence, v1_dir: Path) -> str | None:
    """WHAT: Verifies one negative claim: kind "unreferenced_identifier" (the name is used in no other file) or
    "orphan_file" (no file imports the given file).
    WHY: "Unused" must be shown by the code, not asserted.

    Args:
        item: the claim.
        v1_dir: the sentinel_flow_mvp folder.
    Returns:
        A problem text, or None when the claim holds.
    Example:
        orphan_file components/TelemetryEngine.tsx while a page imports it -> "... is imported by another file"
    """
    if item.kind == "orphan_file":
        target = v1_dir / item.name
        if not target.is_file():
            return f"orphan_file {item.name}: file not found"
        return f"orphan_file {item.name}: is imported by another file" if is_imported_anywhere(v1_dir, target) else None
    defined = v1_dir / item.defined_in
    if not defined.is_file():
        return f"unreferenced_identifier {item.name}: defined_in {item.defined_in} not found"
    if is_identifier_used_elsewhere(v1_dir, item.name, defined):
        return f"unreferenced_identifier {item.name}: is used outside {item.defined_in}"
    return None


def parse_evidence_list(raw: object, label: str, allow_negative: bool = False) -> list[Evidence | UnusedEvidence]:
    """WHAT: Validates a list of evidence items: code items as in feature_truth, plus (if allowed) the negative
    kinds unreferenced_identifier {name, defined_in} and orphan_file {name}.
    WHY: Every claim in screens.yaml needs verifiable evidence.

    Args:
        raw: the YAML list.
        label: owner description for error messages (page, action or call).
        allow_negative: whether negative kinds are accepted (only for unused_calls).
    Returns:
        Parsed evidence objects.
    Raises:
        ScreenInventoryError: for an empty list or a malformed item.
    Example:
        [{"type": "code", "file": "a.tsx", "line": 3, "contains": "x"}] -> [Evidence(kind="code", ...)]
    """
    if not isinstance(raw, list) or not raw:
        raise ScreenInventoryError(f"{label}: needs a non-empty evidence list")
    parsed: list[Evidence | UnusedEvidence] = []
    for item in raw:
        kind = item.get("type") if isinstance(item, dict) else None
        if allow_negative and kind in ("unreferenced_identifier", "orphan_file"):
            name = item.get("name")
            if not name or (kind == "unreferenced_identifier" and not item.get("defined_in")):
                raise ScreenInventoryError(f"{label}: {kind} needs name (and defined_in for identifiers)")
            parsed.append(UnusedEvidence(kind, name, item.get("defined_in", "")))
            continue
        try:
            parsed.append(parse_evidence(item, label))
        except FeatureTruthError as error:
            raise ScreenInventoryError(str(error)) from error
    return parsed


def load_screens(path: Path) -> dict:
    """WHAT: Reads screens.yaml and returns the mapping after checking the top-level keys exist.
    WHY: Missing sections would silently skip checks.

    Args:
        path: the YAML file.
    Returns:
        The parsed document.
    Raises:
        ScreenInventoryError: if a required top-level key is missing.
    Example:
        a file with pages, unused_calls, reviewed_non_literal_calls, hard_coded_data, must_keep, rebuild_in_v2 -> that dict
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    required = ["pages", "unused_calls", "reviewed_non_literal_calls", "hard_coded_data", "must_keep", "rebuild_in_v2"]
    if not isinstance(document, dict):
        raise ScreenInventoryError(f"{path}: expected a mapping")
    for key in required:
        if not isinstance(document.get(key), list):
            raise ScreenInventoryError(f"{path}: missing list '{key}'")
    return document


def split_call_reference(reference: str) -> tuple[str, str]:
    """WHAT: Splits an api_calls reference "POST /api/v1/x" or "/api/v1/x" into (method or '', path).
    WHY: Actions may name the method; the checker compares it with the discovered one.

    Args:
        reference: text from screens.yaml.
    Returns:
        (method or "", normalised path).
    Example:
        "POST /api/v1/suppliers/" -> ("POST", "/api/v1/suppliers")
    """
    parts = reference.split(" ", 1)
    if len(parts) == 2 and parts[0] in HTTP_METHODS:
        return parts[0], normalise_frontend_path(parts[1])
    return "", normalise_frontend_path(reference)


def check_coverage(document: dict, pages: list[Page], calls: list[FrontendCall],
                   non_literal: list[tuple[str, int, str]], feature_status: dict[str, str]) -> list[str]:
    """WHAT: Checks completeness: every page has an entry; every discovered call path appears in some action's
    api_calls or in unused_calls; every api_calls reference and its method exist among the discovered calls;
    every non-literal call is acknowledged; every referenced feature id exists; must_keep entries link only to
    working/broken/untested features and rebuild_in_v2 entries only to mocked/missing ones.
    WHY: Nothing may be silently dropped, and mocked features must not be listed as if they worked.

    Args:
        document: parsed screens.yaml.
        pages: discovered pages.
        calls: discovered frontend calls.
        non_literal: discovered non-literal call lines.
        feature_status: feature id -> status.
    Returns:
        A list of problems (empty when complete).
    Example:
        a page missing from the YAML -> ["page not in screens.yaml: /history"]
    """
    problems = check_pages(document, pages)
    problems += check_call_coverage(document, calls)
    problems += check_non_literal(document, non_literal)
    problems += check_feature_links(document, feature_status)
    return problems


def check_pages(document: dict, pages: list[Page]) -> list[str]:
    """WHAT: Compares discovered pages with the YAML's pages (by route and file); reports missing, unknown
    and duplicated entries.
    WHY: Every screen must be described exactly once.

    Args:
        document: parsed screens.yaml.
        pages: discovered pages.
    Returns:
        Problems.
    Example:
        YAML lacks /history -> ["page not in screens.yaml: /history"]
    """
    problems: list[str] = []
    entries = [(entry.get("route"), entry.get("file")) for entry in document["pages"]]
    for page in pages:
        if (page.route, page.file) not in entries:
            problems.append(f"page not in screens.yaml: {page.route} ({page.file})")
    discovered = {(page.route, page.file) for page in pages}
    for entry in entries:
        if entry not in discovered:
            problems.append(f"screens.yaml page not found in v1: {entry[0]} ({entry[1]})")
        if entries.count(entry) > 1:
            problems.append(f"screens.yaml page duplicated: {entry[0]}")
    return sorted(set(problems))


def check_call_coverage(document: dict, calls: list[FrontendCall]) -> list[str]:
    """WHAT: Checks every discovered call path is used by an action or listed in unused_calls, and every
    reference in the YAML names a discovered call (and, when a method is given, a discovered method).
    WHY: Completeness of the API usage inventory.

    Args:
        document: parsed screens.yaml.
        calls: discovered frontend calls.
    Returns:
        Problems.
    Example:
        a discovered path in neither list -> ["call not covered: /api/v1/inventory/triage"]
    """
    referenced: list[tuple[str, str]] = []
    for page in document["pages"]:
        for action in page.get("user_actions") or []:
            referenced += [split_call_reference(text) for text in action.get("api_calls") or []]
        for shown in page.get("data_shown") or []:
            if shown.get("api_call"):
                referenced.append(split_call_reference(shown["api_call"]))
    referenced += [split_call_reference(item.get("call", "")) for item in document["unused_calls"]]
    problems: list[str] = []
    discovered_paths = {call.path for call in calls}
    for path in sorted(discovered_paths):
        if path not in {ref_path for _, ref_path in referenced}:
            problems.append(f"call not covered by any action or unused_calls: {path}")
    for method, path in referenced:
        methods = {call.method for call in calls if call.path == path}
        if path not in discovered_paths:
            problems.append(f"screens.yaml names a call that v1 does not make: {path}")
        elif method and METHOD_NOT_DETERMINED not in methods and method not in methods:
            problems.append(f"{path}: screens.yaml says {method} but the code uses {sorted(methods)}")
    return problems


def check_non_literal(document: dict, non_literal: list[tuple[str, int, str]]) -> list[str]:
    """WHAT: Checks every non-literal call found in the code is acknowledged in reviewed_non_literal_calls
    (by file and line), and that every acknowledgement points at a real one.
    WHY: Calls the scan cannot follow must be looked at by a person, not ignored.

    Args:
        document: parsed screens.yaml.
        non_literal: discovered non-literal call lines.
    Returns:
        Problems.
    Example:
        an unacknowledged `fetch(url)` at api.ts:204 -> ["non-literal call not reviewed: frontend/src/services/api.ts:204"]
    """
    reviewed = {(item.get("file"), item.get("line")) for item in document["reviewed_non_literal_calls"]}
    found = {(file, line) for file, line, _ in non_literal}
    problems = [f"non-literal call not reviewed: {file}:{line}" for file, line in sorted(found - reviewed)]
    problems += [f"reviewed non-literal call not found: {file}:{line}" for file, line in sorted(reviewed - found, key=str)]
    return problems


def check_feature_links(document: dict, feature_status: dict[str, str]) -> list[str]:
    """WHAT: Checks every feature id used in pages, actions, must_keep and rebuild_in_v2 exists, and applies
    the status rules for must_keep (working/broken/untested) and rebuild_in_v2 (mocked/missing).
    WHY: A mocked feature listed as must_keep would claim v1 behaviour that is fake.

    Args:
        document: parsed screens.yaml.
        feature_status: feature id -> status.
    Returns:
        Problems.
    Example:
        a user action that names an unknown feature id -> ["/pos: unknown feature id foo"]
    """
    problems: list[str] = []
    for page in document["pages"]:
        ids = list(page.get("linked_features") or [])
        for action in page.get("user_actions") or []:
            ids += action.get("features") or []
        problems += [f"{page.get('route')}: unknown feature id {feature_id}" for feature_id in ids if feature_id not in feature_status]
    problems += check_list_entries(document, "must_keep", MUST_KEEP_STATUSES, "rebuild_in_v2", feature_status)
    problems += check_list_entries(document, "rebuild_in_v2", REBUILD_STATUSES, "must_keep", feature_status)
    return problems


def check_list_entries(document: dict, list_name: str, allowed: list[str], other_list: str,
                       feature_status: dict[str, str]) -> list[str]:
    """WHAT: Checks the entries of must_keep or rebuild_in_v2: the feature id exists with an allowed status, and
    the named page and action really exist in screens.yaml (unless the page is one of NON_PAGE_SCOPES).
    WHY: A mocked feature listed as must_keep would claim v1 behaviour that is fake, and a list entry that
    names an action nobody described would be unsupported.

    Args:
        document: parsed screens.yaml.
        list_name: "must_keep" or "rebuild_in_v2".
        allowed: statuses this list accepts.
        other_list: the name of the other list, used in the message.
        feature_status: feature id -> status.
    Returns:
        Problems.
    Example:
        must_keep linking to auto-po (mocked) -> ["must_keep '...': feature auto-po is mocked; list it under rebuild_in_v2"]
    """
    actions_by_route = {page.get("route"): {action.get("name") for action in page.get("user_actions") or []}
                        for page in document["pages"]}
    problems: list[str] = []
    for entry in document[list_name]:
        label = f"{list_name} '{entry.get('action')}'"
        status = feature_status.get(entry.get("feature"))
        if status is None:
            problems.append(f"{label}: unknown feature id {entry.get('feature')}")
        elif status not in allowed:
            problems.append(f"{label}: feature {entry.get('feature')} is {status}; list it under {other_list}")
        page = entry.get("page")
        if page not in NON_PAGE_SCOPES and entry.get("action") not in actions_by_route.get(page, set()):
            problems.append(f"{label}: no action with that name on page {page}")
        if not entry.get("reason"):
            problems.append(f"{label}: needs a reason")
    return problems


def collect_evidence(document: dict) -> list[tuple[str, list[Evidence | UnusedEvidence]]]:
    """WHAT: Parses every evidence list in screens.yaml (actions, data_shown, unused_calls, hard_coded_data,
    reviewed_non_literal_calls) into (label, items) pairs; negative kinds are allowed only for unused_calls.
    WHY: One place validates the shape of all evidence.

    Args:
        document: parsed screens.yaml.
    Returns:
        (label, parsed evidence list) pairs.
    Raises:
        ScreenInventoryError: for a malformed evidence list.
    Example:
        a page with 3 actions and 2 data items -> 5 pairs from that page
    """
    pairs: list[tuple[str, list[Evidence | UnusedEvidence]]] = []
    for page in document["pages"]:
        route = page.get("route")
        for action in page.get("user_actions") or []:
            pairs.append((f"{route} action '{action.get('name')}'", parse_evidence_list(action.get("evidence"), f"{route} action '{action.get('name')}'")))
        for shown in page.get("data_shown") or []:
            pairs.append((f"{route} data '{shown.get('what')}'", parse_evidence_list(shown.get("evidence"), f"{route} data '{shown.get('what')}'")))
    for item in document["unused_calls"]:
        pairs.append((f"unused call {item.get('call')}", parse_evidence_list(item.get("evidence"), f"unused call {item.get('call')}", allow_negative=True)))
    for item in document["hard_coded_data"] + document["reviewed_non_literal_calls"]:
        label = f"{item.get('where') or item.get('file')}:{item.get('line', '')}"
        pairs.append((label, parse_evidence_list(item.get("evidence"), label)))
    return pairs


def check_evidence(document: dict, v1_dir: Path) -> list[str]:
    """WHAT: Verifies every evidence item: code items through feature_truth's checker, negative items with the
    import and identifier scans. Returns ALL problems.
    WHY: Every action, data source and hard-coded value must hold up against the code.

    Args:
        document: parsed screens.yaml.
        v1_dir: the sentinel_flow_mvp folder.
    Returns:
        Problems.
    Example:
        a wrong line number -> ["<label>: frontend/src/app/pos/page.tsx:9 does not contain ..."]
    """
    problems: list[str] = []
    for label, items in collect_evidence(document):
        for item in items:
            if isinstance(item, UnusedEvidence):
                problem = check_unused_evidence(item, v1_dir)
            else:
                problem = check_one_evidence(label, item, v1_dir, Path("."), {})
            if problem is not None:
                problems.append(problem)
    return problems


def render_screens(document: dict, matches: list[CallMatch], any_route_group: bool,
                   non_literal: list[tuple[str, int, str]], feature_status: dict[str, str]) -> str:
    """WHAT: Renders screens.md: a page-by-page summary, the dead-call table (computed from the match results)
    plus the declared unused calls, the hard-coded-data table, the must_keep list and the "rebuild properly in
    v2" list, and the non-literal calls to check by hand.
    WHY: Generated, human-readable output; never edited by hand.

    Args:
        document: parsed screens.yaml.
        matches: call/route match results.
        any_route_group: whether any route group folder exists.
        non_literal: discovered non-literal call lines.
        feature_status: feature id -> status.
    Returns:
        Markdown text.
    Example:
        8 pages and 2 dead calls -> a page section per route and a 2-row dead-call table
    """
    lines = ["# v1 screen and function inventory", "", "_Generated by tools/phase0/screen_inventory.py. Do not edit by hand._", "",
             "Records what each screen does, not how it looks (v2 gets a new design).", "",
             f"Route groups in the app folder: {'yes' if any_route_group else 'none'}.", "", "## Pages", ""]
    for page in document["pages"]:
        lines += render_page(page, feature_status)
    lines += render_call_tables(document, matches, non_literal)
    lines += render_lists(document, feature_status)
    return chr(10).join(lines) + chr(10)


def render_page(page: dict, feature_status: dict[str, str]) -> list[str]:
    """WHAT: Renders one page section: purpose, actions (with API calls and features), data shown, components.
    WHY: Keeps render_screens short.

    Args:
        page: one page mapping from screens.yaml.
        feature_status: feature id -> status.
    Returns:
        Markdown lines.
    Example:
        a page with 2 actions -> a heading, the purpose and a 2-row actions table
    """
    lines = [f"### {page['route']}  (`{page['file']}`)", "", page["purpose"], "",
             "| action | where | API calls | features |", "|---|---|---|---|"]
    for action in page.get("user_actions") or []:
        where = action["evidence"][0]
        place = f"{where.get('file')}:{where.get('line')}"
        calls = ", ".join(action.get("api_calls") or []) or "none (local only)"
        features = ", ".join(f"{fid} ({feature_status.get(fid)})" for fid in action.get("features") or []) or "UI only"
        lines.append(f"| {action['name']} | {place} | {calls} | {features} |")
    lines += ["", "Data shown:", ""]
    for shown in page.get("data_shown") or []:
        source = shown["source"] + (f" ({shown['api_call']})" if shown.get("api_call") else "")
        lines.append(f"- {shown['what']} - {source}")
    lines += ["", "Components: " + ", ".join(page.get("components") or []), ""]
    return lines


def render_call_tables(document: dict, matches: list[CallMatch], non_literal: list[tuple[str, int, str]]) -> list[str]:
    """WHAT: Renders the dead-call table (frontend calls with no backend route), the method problems, the
    declared unused calls with reasons, the hard-coded-data table and the non-literal calls to check by hand.
    WHY: These are the places where v1's frontend and backend disagree or fake data.

    Args:
        document: parsed screens.yaml.
        matches: call/route match results.
        non_literal: discovered non-literal call lines.
    Returns:
        Markdown lines.
    Example:
        one dead call -> a table row with its file:line
    """
    dead = [match for match in matches if match.status == "dead"]
    problems = [match for match in matches if match.status == "method mismatch"]
    lines = ["## Dead calls (frontend calls with no backend route)", "", "| path | method | where |", "|---|---|---|"]
    lines += [f"| {m.call.path} | {m.call.method} | {m.call.file}:{m.call.line} |" for m in dead] or ["| none | | |"]
    lines += ["", "## Method mismatches", "", "| path | frontend method | backend routes | where |", "|---|---|---|---|"]
    lines += [f"| {m.call.path} | {m.call.method} | {', '.join(m.backend_routes)} | {m.call.file}:{m.call.line} |" for m in problems] or ["| none | | | |"]
    lines += ["", "## Calls no screen can reach (unused_calls)", "", "| call | reason |", "|---|---|"]
    lines += [f"| {item['call']} | {item['reason']} |" for item in document["unused_calls"]] or ["| none | |"]
    lines += ["", "## Hard-coded data", "", "| where | what | effect |", "|---|---|---|"]
    lines += [f"| {item['where']} | {item['what']} | {item['effect']} |" for item in document["hard_coded_data"]]
    lines += ["", "## Calls whose URL is not a plain string (checked by hand)", "", "| where | note |", "|---|---|"]
    notes = {(item["file"], item["line"]): item["reason"] for item in document["reviewed_non_literal_calls"]}
    lines += [f"| {file}:{line} | {notes.get((file, line), 'NOT REVIEWED')} |" for file, line, _ in non_literal]
    return lines


def render_lists(document: dict, feature_status: dict[str, str]) -> list[str]:
    """WHAT: Renders the must_keep list (actions v2 must keep) and the "rebuild properly in v2" list (actions
    whose v1 feature is mocked or missing), each with feature, status and reason.
    WHY: v2 must lose no useful function, and must not copy fake behaviour.

    Args:
        document: parsed screens.yaml.
        feature_status: feature id -> status.
    Returns:
        Markdown lines.
    Example:
        one must_keep entry -> a row "pos: add item | pos-checkout (working) | reason"
    """
    lines = ["", "## must_keep for v2", "", "| action | feature | reason |", "|---|---|---|"]
    lines += [f"| {e['page']}: {e['action']} | {e['feature']} ({feature_status.get(e['feature'])}) | {e['reason']} |" for e in document["must_keep"]]
    lines += ["", "## Rebuild properly in v2 (v1 version is mocked or missing)", "", "| action | feature | reason |", "|---|---|---|"]
    lines += [f"| {e['page']}: {e['action']} | {e['feature']} ({feature_status.get(e['feature'])}) | {e['reason']} |" for e in document["rebuild_in_v2"]]
    return lines


def audit(v1_dir: Path, directory: Path, features_path: Path) -> int:
    """WHAT: Runs discovery, the call/route match, coverage and evidence checks, and (only if all pass) renders
    screens.md. Prints every problem.
    WHY: Separates the audit from cloning.

    Args:
        v1_dir: the sentinel_flow_mvp folder of the clone.
        directory: folder holding screens.yaml and receiving screens.md.
        features_path: docs/phase0/feature-truth/features.yaml.
    Returns:
        0 when everything holds, otherwise 1.
    Raises:
        ScreenInventoryError: for malformed YAML.
    Example:
        a consistent audit -> 0
    """
    document = load_screens(directory / "screens.yaml")
    feature_status = {feature.id: feature.status for feature in load_features(features_path)}
    pages, any_group = discover_pages(v1_dir)
    calls = discover_frontend_calls(v1_dir)
    non_literal = discover_non_literal_calls(v1_dir)
    matches = match_calls_to_routes(calls, [item.key for item in discover_inventory(v1_dir)])
    problems = check_coverage(document, pages, calls, non_literal, feature_status) + check_evidence(document, v1_dir)
    for problem in problems:
        print("PROBLEM:", problem)
    if problems:
        return 1
    (directory / "screens.md").write_text(render_screens(document, matches, any_group, non_literal, feature_status), encoding="utf-8")
    print(f"OK: {len(pages)} pages, {len({c.path for c in calls})} distinct call paths, "
          f"{sum(1 for m in matches if m.status == 'dead')} dead call(s), all evidence verified")
    return 0


def main(argv: list[str]) -> int:
    """WHAT: Clones v1-final fresh (line-ending conversion off) into a temp folder, runs the audit and returns
    its exit code. The temp folder is always removed.
    WHY: Re-checking against a fresh clone proves the inventory is about the tagged code.

    Args:
        argv: command-line arguments.
    Returns:
        0 when everything holds, otherwise 1.
    Side effects:
        Writes screens.md (only when all checks pass).
    Example:
        main([]) with a consistent screens.yaml -> prints "OK: ..." and returns 0
    """
    parser = argparse.ArgumentParser(description="Inventory v1's screens and the API calls they make.")
    parser.add_argument("--tag", default=V1_DEFAULT_TAG)
    parser.add_argument("--dir", default="docs/phase0/screens")
    parser.add_argument("--features", default="docs/phase0/feature-truth/features.yaml")
    args = parser.parse_args(argv)
    work_root = Path(tempfile.mkdtemp(prefix="screen_inventory_"))
    try:
        v1_dir = clone_exact(args.tag, work_root / "v1", shallow=True) / "sentinel_flow_mvp"
        return audit(v1_dir, Path(args.dir), Path(args.features))
    finally:
        shutil.rmtree(work_root, onerror=make_writable_and_retry)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
