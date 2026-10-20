# TERM: AST - the tree Python builds from source code; we read it instead of using regexes.
# TERM: real code line - a line that is not blank, not a comment and not part of a docstring.
# TERM: type hint - a note such as `x: int` or `-> str` that says what a value should be.
"""Checks that every new file of this task follows the written code standard."""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Every file created in the Phase 0 baseline task (keep this list up to date).
NEW_CODE_FILES = [
    "tools/phase0/run_v1_suite.py",
    "tools/phase0/run_v1_suite_docker.py",
    "tools/phase0/feature_truth.py",
    "tests/phase0/test_parse_junit.py",
    "tests/phase0/test_pip_log.py",
    "tests/phase0/test_docker_helpers.py",
    "tests/phase0/test_feature_truth.py",
    "tests/v1_flaws/scorecard_adapter.py",
    "tests/v1_flaws/test_scorecard_flaws.py",
    "tests/phase0/test_package_diff.py",
    "tests/test_code_standard.py",
]
# policy: the written code standard allows at most 30 real code lines per function.
MAX_FUNCTION_LINES = 30
REQUIRED_DOCSTRING_WORDS = ["WHAT", "WHY", "Args", "Returns", "Example"]


def load_functions(relative_path: str) -> tuple[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]:
    """WHAT: Reads a file and returns its text plus every function defined in it.
    WHY: All the rule checks below need the same two inputs.

    Args:
        relative_path: path relative to the repo root.
    Returns:
        (source text, list of function nodes including methods and nested ones).
    Example:
        load_functions("tools/phase0/run_v1_suite.py") -> (text, [<main>, ...])
    """
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return source, functions


def count_real_lines(function: ast.FunctionDef | ast.AsyncFunctionDef, source_lines: list[str]) -> int:
    """WHAT: Counts a function's lines, skipping blanks, comments and its docstring.
    WHY: The 30-line limit is about logic, not about documentation.

    Args:
        function: the function node.
        source_lines: the file split into lines.
    Returns:
        Number of real code lines.
    Example:
        a def line + 3 statements + 2 blank + 1 comment + 4 docstring lines -> 4
    """
    docstring_lines: set[int] = set()
    first_statement = function.body[0]
    if isinstance(first_statement, ast.Expr) and isinstance(first_statement.value, ast.Constant):
        if isinstance(first_statement.value.value, str):
            docstring_lines = set(range(first_statement.lineno, first_statement.end_lineno + 1))
    count = 0
    for line_number in range(function.lineno, function.end_lineno + 1):
        stripped = source_lines[line_number - 1].strip()
        if stripped and not stripped.startswith("#") and line_number not in docstring_lines:
            count += 1
    return count


def has_all_type_hints(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """WHAT: True when every parameter (except self/cls) and the return value have hints.
    WHY: Hints are part of the standard and make signatures self-explaining.

    Args:
        function: the function node.
    Returns:
        True if fully hinted.
    Example:
        def f(x: int) -> str -> True; def f(x) -> False
    """
    arguments = function.args
    every_argument = arguments.posonlyargs + arguments.args + arguments.kwonlyargs
    for argument in every_argument:
        if argument.arg not in ("self", "cls") and argument.annotation is None:
            return False
    return function.returns is not None


def find_violations(check: str) -> list[str]:
    """WHAT: Runs one rule ("length", "hints", "docstring" or "term") over NEW_CODE_FILES.
    WHY: One loop serves every rule, so each rule can be its own small test.

    Args:
        check: which rule to run.
    Returns:
        Human-readable violation strings; empty when everything passes.
    Example:
        check="length" and one 45-line function -> ["file.py:10 big_function has 45 real lines (max 30)"]
    """
    violations: list[str] = []
    for relative_path in NEW_CODE_FILES:
        source, functions = load_functions(relative_path)
        if check == "term" and not source.startswith("# TERM:"):
            violations.append(f"{relative_path}: must begin with '# TERM:' lines")
        for function in functions if check != "term" else []:
            problem = describe_problem(check, function, source.splitlines())
            if problem:
                violations.append(f"{relative_path}:{function.lineno} {function.name} {problem}")
    return violations


def describe_problem(
    check: str, function: ast.FunctionDef | ast.AsyncFunctionDef, source_lines: list[str]
) -> str:
    """WHAT: Applies one rule to one function and describes the problem, or '' if fine.
    WHY: Keeps find_violations short and each rule easy to read.

    Args:
        check: "length", "hints" or "docstring".
        function: the function node.
        source_lines: file lines.
    Returns:
        A problem description or an empty string.
    Example:
        a function with no docstring and check="docstring" -> "has no docstring"
    """
    if check == "length":
        real_lines = count_real_lines(function, source_lines)
        if real_lines > MAX_FUNCTION_LINES:
            return f"has {real_lines} real lines (max {MAX_FUNCTION_LINES})"
    if check == "hints" and not has_all_type_hints(function):
        return "is missing type hints"
    if check == "docstring":
        docstring = ast.get_docstring(function) or ""
        missing = [word for word in REQUIRED_DOCSTRING_WORDS if word not in docstring]
        if missing:
            return f"docstring is missing {missing}"
    return ""


def test_files_exist_and_are_listed() -> None:
    """WHAT: Checks every path in NEW_CODE_FILES exists.
    WHY: A renamed file would otherwise silently drop out of the standard check.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        a list entry with a typo -> assertion fails naming the file
    """
    missing = [path for path in NEW_CODE_FILES if not (REPO_ROOT / path).is_file()]
    assert missing == []


def test_no_function_is_too_long() -> None:
    """WHAT: Fails if any function has more than 30 real code lines.
    WHY: Short functions are easier to read, test and review.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        a 45-line function -> failure message naming it
    """
    assert find_violations("length") == []


def test_every_function_has_type_hints() -> None:
    """WHAT: Fails if any parameter or return value lacks a type hint.
    WHY: Hints document what each function expects and gives back.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        def f(x): ... -> failure naming f
    """
    assert find_violations("hints") == []


def test_every_docstring_has_required_sections() -> None:
    """WHAT: Fails if a function docstring lacks WHAT, WHY, Args, Returns or Example.
    WHY: The standard requires every function to explain itself with a real example.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        a docstring without "Example" -> failure listing ["Example"]
    """
    assert find_violations("docstring") == []


def test_every_file_starts_with_term_lines() -> None:
    """WHAT: Fails if a file does not begin with '# TERM:' lines.
    WHY: Jargon must be defined at the top of each file for new readers.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        a file starting with "import os" -> failure naming the file
    """
    assert find_violations("term") == []
