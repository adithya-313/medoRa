"""Test-change gate for CI.

Inspects changes from git diff --name-status and identifies test or CI gate modifications
that require explicit human review.
"""

from __future__ import annotations

import sys


def parse_name_status(text: str) -> list[tuple[str, str]]:
    """Parse output from `git diff --name-status`.

    Args:
        text: Raw output from git diff.

    Returns:
        A list of (status, path) pairs. For renames, both the old path and
        new path are included as separate entries with the rename status.

    Raises:
        ValueError: If any line does not conform to expected git diff name-status format.
    """
    lines = text.strip().splitlines()
    if not lines:
        return []

    results: list[tuple[str, str]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0].strip()
        if not status:
            raise ValueError(f"Malformed line with empty status: {line!r}")

        # Rename lines have format: <status>\t<old_path>\t<new_path>
        if status.startswith("R"):
            if len(parts) != 3:
                raise ValueError(f"Malformed rename line (expected 3 fields, got {len(parts)}): {line!r}")
            old_path = parts[1].strip()
            new_path = parts[2].strip()
            if not old_path or not new_path:
                raise ValueError(f"Malformed rename line with empty path: {line!r}")
            results.append((status, old_path.replace("\\", "/")))
            results.append((status, new_path.replace("\\", "/")))
        else:
            if len(parts) != 2:
                raise ValueError(f"Malformed diff line (expected 2 fields, got {len(parts)}): {line!r}")
            path = parts[1].strip()
            if not path:
                raise ValueError(f"Malformed diff line with empty path: {line!r}")
            results.append((status, path.replace("\\", "/")))

    return results


def decide(changes: list[tuple[str, str]]) -> list[str]:
    """Determine which changed paths require human review.

    Requires review if:
    - Status M, D, or R* on a path under tests/
    - ANY status on a path under .github/workflows/
    - ANY status on tools/ci/test_change_gate.py
    - ANY status on pytest.ini

    Does NOT require review for:
    - Status A under tests/
    - Changes outside tests/, .github/workflows/, pytest.ini, and the gate script itself.

    Args:
        changes: List of (status, path) pairs.

    Returns:
        Sorted list of unique paths requiring human review.
    """
    needs_review: set[str] = set()

    for status, raw_path in changes:
        path = raw_path.replace("\\", "/")

        # Workflows require review on any change
        if path == ".github/workflows" or path.startswith(".github/workflows/"):
            needs_review.add(path)
            continue

        # The test change gate script itself requires review on any change
        if path == "tools/ci/test_change_gate.py":
            needs_review.add(path)
            continue

        # pytest.ini configuration requires review on any change
        if path == "pytest.ini":
            needs_review.add(path)
            continue

        # Tests require review for modifications, deletions, or renames
        if path == "tests" or path.startswith("tests/"):
            if status == "M" or status == "D" or status.startswith("R"):
                needs_review.add(path)
            elif status == "A":
                # Newly added tests do not require test-change review
                continue
            else:
                # Other non-add statuses on tests
                needs_review.add(path)

    return sorted(needs_review)


def main() -> int:
    """Read git diff from stdin, print paths requiring review, and exit with code 0 or 3.

    Returns:
        0 if no paths require review, 3 if at least one path requires review.
    """
    text = sys.stdin.read()
    changes = parse_name_status(text)
    review_paths = decide(changes)
    if review_paths:
        for path in review_paths:
            print(path)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
