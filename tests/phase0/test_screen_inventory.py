# TERM: App Router - Next.js folder rule: src/app/foo/page.tsx is served at /foo.
# TERM: dead call - a frontend call to an /api/v1 path with no backend route.
# TERM: orphan file - a source file that no other frontend file imports.
# TERM: fixture tree - a tiny fake v1 folder written into pytest's temp folder for one test.
"""Unit tests for tools/phase0/screen_inventory.py. Tiny fixtures; no network, no git, no Docker."""
from __future__ import annotations

from pathlib import Path

from tools.phase0.screen_inventory import (
    METHOD_NOT_DETERMINED,
    FrontendCall,
    Page,
    UnusedEvidence,
    check_coverage,
    check_unused_evidence,
    discover_frontend_calls,
    discover_non_literal_calls,
    discover_pages,
    match_calls_to_routes,
    normalise_backend_path,
    normalise_frontend_path,
    page_route,
)

BACKEND = ["GET /api/v1/suppliers", "POST /api/v1/suppliers", "GET /api/v1/suppliers/{supplier_id}/orders",
           "POST /api/v1/purchase-orders/receive", "GET /api/v1/purchase-orders/{po_id}"]
STATUS = {"f-working": "working", "f-mocked": "mocked", "f-broken": "broken", "f-missing": "missing"}


def write_frontend(root: Path, files: dict[str, str]) -> Path:
    """WHAT: Writes a fake sentinel_flow_mvp/frontend tree from a {relative path: text} map.
    WHY: Discovery tests need real files but not the real v1 clone.

    Args:
        root: an empty temp folder.
        files: paths relative to the frontend folder, with their text.
    Returns:
        The fake sentinel_flow_mvp folder (root itself).
    Example:
        {"src/app/pos/page.tsx": "x"} -> root/frontend/src/app/pos/page.tsx
    """
    for relative, text in files.items():
        path = root / "frontend" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def make_call(path: str, method: str) -> FrontendCall:
    """WHAT: Builds a FrontendCall for matching tests.
    WHY: Keeps the matching tests short.

    Args:
        path: normalised path.
        method: HTTP method or METHOD_NOT_DETERMINED.
    Returns:
        A FrontendCall located at a.tsx line 1.
    Example:
        make_call("/api/v1/suppliers", "GET") -> FrontendCall(path="/api/v1/suppliers", ...)
    """
    return FrontendCall(path, path, "a.tsx", 1, method)


def base_document() -> dict:
    """WHAT: Builds a minimal valid screens.yaml mapping with one page, one action, one call and no extras.
    WHY: Coverage tests change one thing at a time.

    Args:
        none.
    Returns:
        The document dict.
    Example:
        base_document()["pages"][0]["route"] -> "/pos"
    """
    action = {"name": "Search", "api_calls": ["GET /api/v1/suppliers"], "features": ["f-working"]}
    page = {"route": "/pos", "file": "frontend/src/app/pos/page.tsx", "user_actions": [action],
            "data_shown": [], "linked_features": ["f-working"]}
    return {"pages": [page], "unused_calls": [], "reviewed_non_literal_calls": [], "hard_coded_data": [],
            "must_keep": [{"page": "/pos", "action": "Search", "feature": "f-working", "reason": "r"}],
            "rebuild_in_v2": []}


def test_route_mapping_from_folder_paths() -> None:
    """WHAT: Checks the App Router rule: folders become the route, the root page is "/", and a route group in
    parentheses is dropped from the route and reported.
    WHY: Screens are identified by their route.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        "admin/settings/page.tsx" -> "/admin/settings"; "(shop)/pos/page.tsx" -> "/pos" with a group
    """
    assert page_route("page.tsx") == ("/", False)
    assert page_route("admin/settings/page.tsx") == ("/admin/settings", False)
    assert page_route("(shop)/pos/page.tsx") == ("/pos", True)


def test_discover_pages_reports_route_groups(tmp_path: Path) -> None:
    """WHAT: In a fake tree with three pages, one inside a route group, discovery returns their routes sorted
    and reports that a route group exists.
    WHY: The task requires saying whether route groups exist.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        pages "/", "/history", "(g)/pos" -> routes ["/", "/history", "/pos"], group True
    """
    root = write_frontend(tmp_path, {"src/app/page.tsx": "x", "src/app/history/page.tsx": "x", "src/app/(g)/pos/page.tsx": "x"})
    pages, any_group = discover_pages(root)
    assert [page.route for page in pages] == ["/", "/history", "/pos"] and any_group is True


def test_template_literal_and_query_string_are_normalised() -> None:
    """WHAT: A ${...} part becomes {param}, a query string is removed, a trailing slash is removed, and backend
    {po_id} also becomes {param}.
    WHY: Frontend and backend paths must be comparable.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        "/api/v1/suppliers/${id}/orders?x=1" -> "/api/v1/suppliers/{param}/orders"
    """
    assert normalise_frontend_path("/api/v1/suppliers/${supplierId}/orders?limit=${n}") == "/api/v1/suppliers/{param}/orders"
    assert normalise_frontend_path("/api/v1/audit-logs?${params.toString()}") == "/api/v1/audit-logs"
    assert normalise_frontend_path("/api/v1/notifications/") == "/api/v1/notifications"
    assert normalise_backend_path("/api/v1/purchase-orders/{po_id}") == "/api/v1/purchase-orders/{param}"


def test_call_discovery_reads_paths_methods_and_skips_comments(tmp_path: Path) -> None:
    """WHAT: In a fake file, an api.post call, a multi-line fetch with method: 'PUT', a plain fetch (GET) and a
    path inside a comment are found/skipped correctly, and a fetch with a variable URL is listed as non-literal.
    WHY: The scan must read methods where it can and flag what it cannot follow.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        api.post('/api/v1/suppliers', {...}) -> POST /api/v1/suppliers; "// /api/v1/ignored" is skipped
    """
    text = (
        "// see '/api/v1/ignored'\n"
        "await api.post('/api/v1/suppliers', { a: 1 });\n"
        "await fetch('/api/v1/settings', {\n"
        "  method: 'PUT',\n"
        "  body: x,\n"
        "});\n"
        "await fetch(`/api/v1/suppliers/${id}/orders`);\n"
        "await fetch(url, { headers: h });\n"
    )
    root = write_frontend(tmp_path, {"src/services/x.ts": text})
    found = {(call.path, call.method) for call in discover_frontend_calls(root)}
    assert found == {("/api/v1/suppliers", "POST"), ("/api/v1/settings", "PUT"), ("/api/v1/suppliers/{param}/orders", "GET")}
    assert [line for _, line, _ in discover_non_literal_calls(root)] == [8]


def test_dead_call_and_method_mismatch_and_undetermined_method() -> None:
    """WHAT: A call with no backend path is "dead"; a PUT to a path that only has GET/POST is a "method
    mismatch"; a call whose method is unreadable is "method not determined"; a correct call is "matched";
    the literal route wins over the {param} route.
    WHY: These are the contract problems between frontend and backend.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        POST /api/v1/purchase-orders/receive matches the literal route, not GET /{po_id}
    """
    calls = [make_call("/api/v1/nothing", "GET"), make_call("/api/v1/suppliers", "PUT"),
             make_call("/api/v1/suppliers", METHOD_NOT_DETERMINED), make_call("/api/v1/suppliers", "POST"),
             make_call("/api/v1/purchase-orders/receive", "POST")]
    results = [match.status for match in match_calls_to_routes(calls, BACKEND)]
    assert results == ["dead", "method mismatch", "method not determined", "matched", "matched"]
    last = match_calls_to_routes([calls[-1]], BACKEND)[0]
    assert last.backend_routes == ["POST /api/v1/purchase-orders/receive"]


def test_coverage_fails_for_missing_page_uncovered_call_and_unknown_feature() -> None:
    """WHAT: A discovered page missing from the YAML, a discovered call used by no action, an unknown feature
    id and an unreviewed non-literal call each produce a problem; the base document has none.
    WHY: Nothing may be silently dropped.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        page /history discovered but absent -> "page not in screens.yaml: /history"
    """
    pages = [Page("/pos", "frontend/src/app/pos/page.tsx")]
    calls = [make_call("/api/v1/suppliers", "GET")]
    assert check_coverage(base_document(), pages, calls, [], STATUS) == []
    missing_page = check_coverage(base_document(), pages + [Page("/history", "frontend/src/app/history/page.tsx")], calls, [], STATUS)
    assert missing_page == ["page not in screens.yaml: /history (frontend/src/app/history/page.tsx)"]
    uncovered = check_coverage(base_document(), pages, calls + [make_call("/api/v1/other", "GET")], [], STATUS)
    assert any("call not covered" in problem and "/api/v1/other" in problem for problem in uncovered)
    document = base_document()
    document["pages"][0]["user_actions"][0]["features"] = ["f-ghost"]
    assert any("unknown feature id f-ghost" in problem for problem in check_coverage(document, pages, calls, [], STATUS))
    unreviewed = check_coverage(base_document(), pages, calls, [("a.ts", 5, "fetch(url)")], STATUS)
    assert any("non-literal call not reviewed: a.ts:5" in problem for problem in unreviewed)


def test_must_keep_rejects_mocked_and_unknown_actions() -> None:
    """WHAT: A must_keep entry linked to a mocked feature is rejected; a rebuild_in_v2 entry linked to a working
    feature is rejected; a must_keep entry naming an action that the page does not have is rejected.
    WHY: Fake features must not be listed as things v2 should keep, and entries must be backed by real actions.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        must_keep -> f-mocked (mocked) -> "feature f-mocked is mocked; list it under rebuild_in_v2"
    """
    pages = [Page("/pos", "frontend/src/app/pos/page.tsx")]
    calls = [make_call("/api/v1/suppliers", "GET")]
    document = base_document()
    document["must_keep"].append({"page": "/pos", "action": "Search", "feature": "f-mocked", "reason": "r"})
    document["must_keep"].append({"page": "/pos", "action": "Ghost action", "feature": "f-working", "reason": "r"})
    document["rebuild_in_v2"].append({"page": "none", "action": "x", "feature": "f-working", "reason": "r"})
    problems = check_coverage(document, pages, calls, [], STATUS)
    assert any("feature f-mocked is mocked; list it under rebuild_in_v2" in problem for problem in problems)
    assert any("no action with that name on page /pos" in problem for problem in problems)
    assert any("feature f-working is working; list it under must_keep" in problem for problem in problems)


def test_orphan_file_and_unreferenced_identifier_checks(tmp_path: Path) -> None:
    """WHAT: In a fake tree, a file nobody imports is an orphan, a file that another file imports is not, an
    identifier used in no other file is unreferenced, and one that another file mentions is not.
    WHY: "Unused" claims must be proven by the code.

    Args:
        tmp_path: pytest temporary folder.
    Returns:
        Nothing.
    Example:
        components/Used.tsx imported by a page -> the orphan claim for Used.tsx fails
    """
    root = write_frontend(tmp_path, {
        "src/app/pos/page.tsx": "import Used from '@/components/Used';" + chr(10) + "useIt();" + chr(10),
        "src/components/Used.tsx": "export default 1;",
        "components/Lonely.tsx": "export default 2;",
        "src/services/api.ts": "export const api = { usedFn: 1, unusedFn: 2 };",
    })
    assert check_unused_evidence(UnusedEvidence("orphan_file", "frontend/components/Lonely.tsx", ""), root) is None
    assert "imported by another file" in (check_unused_evidence(UnusedEvidence("orphan_file", "frontend/src/components/Used.tsx", ""), root) or "")
    assert check_unused_evidence(UnusedEvidence("unreferenced_identifier", "unusedFn", "frontend/src/services/api.ts"), root) is None
    assert "used outside" in (check_unused_evidence(UnusedEvidence("unreferenced_identifier", "useIt", "frontend/src/services/api.ts"), root) or "")
