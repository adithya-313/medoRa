# TERM: --out - a command-line option that makes a checker render its output into another folder.
# TERM: fixture tree - a tiny fake v1 folder or medoRa docs folder written into pytest's temp folder.
# TERM: monkeypatch - pytest's way of replacing one function (here: the git clone) with a local fake for one test.
"""Each of the four Phase 0 checkers renders into --out and leaves its input folder (--dir) untouched.

The real main() of each checker runs; only the git clone is replaced by a fake that returns a local fixture,
so there is no network.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from tools.phase0 import adr_000, data_provenance, feature_truth, screen_inventory

pytestmark = pytest.mark.unit

CODE_EVIDENCE = {"type": "code", "file": "app/api/good.py", "line": 2, "contains": "@router.get"}


def snapshot(folder: Path) -> dict[str, bytes]:
    """WHAT: Reads every file under a folder into a {relative path: bytes} map.
    WHY: Comparing two snapshots proves a folder was not changed.

    Args:
        folder: folder to read.
    Returns:
        The map (empty if the folder does not exist).
    Example:
        a folder with a.txt containing b"x" -> {"a.txt": b"x"}
    """
    contents: dict[str, bytes] = {}
    if folder.exists():
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                contents[path.relative_to(folder).as_posix()] = path.read_bytes()
    return contents


def write_files(root: Path, files: dict[str, str]) -> None:
    """WHAT: Writes {relative path: text} files under root, creating folders.
    WHY: Builds fixture trees compactly.

    Args:
        root: base folder.
        files: relative paths and their text.
    Returns:
        Nothing.
    Example:
        {"a/b.txt": "x"} -> root/a/b.txt
    """
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def fixture_v1(root: Path) -> Path:
    """WHAT: Builds a tiny fake sentinel_flow_mvp: one mounted route, one service, one worker, one page, a
    requirements file and a data CSV. Returns its folder.
    WHY: Gives every checker a v1 to inspect without the network.

    Args:
        root: an empty folder.
    Returns:
        The fake sentinel_flow_mvp folder.
    Example:
        fixture_v1(tmp_path) -> tmp_path/sentinel_flow_mvp with app/main.py, app/api/good.py, ...
    """
    v1_dir = root / "sentinel_flow_mvp"
    write_files(
        v1_dir,
        {
            "app/main.py": "from app.api import good"
            + chr(10)
            + "def create(app):"
            + chr(10)
            + "    app.include_router(good.router, prefix='/api/v1/good')"
            + chr(10),
            "app/api/good.py": "router = APIRouter()"
            + chr(10)
            + "@router.get('/a')"
            + chr(10)
            + "def read_a():"
            + chr(10)
            + "    return 1"
            + chr(10),
            "app/services/thing.py": "x = 1" + chr(10),
            "app/workers/runner.py": "y = 2" + chr(10),
            "frontend/src/app/page.tsx": "export default 1;" + chr(10),
            "scripts/s.py": "z = 3" + chr(10),
            "requirements.txt": "requests==1" + chr(10),
            "data/a.csv": "id" + chr(10) + "1" + chr(10),
        },
    )
    return v1_dir


def feature_row(entry_points: list[str]) -> dict:
    """WHAT: Builds one valid raw feature mapping with code evidence.
    WHY: The checkers need at least one valid feature.

    Args:
        entry_points: the feature's entry points.
    Returns:
        The mapping.
    Example:
        feature_row(["x"]) -> {"id": "f1", ..., "entry_points": ["x"]}
    """
    return {
        "id": "f1",
        "name": "n",
        "area": "billing",
        "entry_points": entry_points,
        "status": "untested",
        "evidence": [CODE_EVIDENCE],
    }


def test_feature_truth_out_writes_there_and_leaves_dir_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WHAT: Runs feature_truth.main with --out and checks feature-truth.md appears in --out while the --dir files
    are byte-for-byte unchanged and no table appears in --dir.
    WHY: The showcase renders into a temp folder and must never overwrite the committed table.

    Args:
        tmp_path: pytest temporary folder.
        monkeypatch: pytest monkeypatch.
    Returns:
        Nothing.
    Example:
        --out tmp/out -> tmp/out/feature-truth.md exists; tmp/in has only features.yaml and ignored.yaml
    """
    v1_dir = fixture_v1(tmp_path / "v1root")
    monkeypatch.setattr(feature_truth, "clone_v1", lambda tag, target: v1_dir)
    inputs = tmp_path / "in"
    entry_points = [
        "GET /api/v1/good/a",
        "app/services/thing.py",
        "app/workers/runner.py",
        "frontend/src/app/page.tsx",
    ]
    write_files(
        inputs,
        {
            "features.yaml": yaml.safe_dump({"features": [feature_row(entry_points)]}),
            "ignored.yaml": "ignored: []" + chr(10),
        },
    )
    before = snapshot(inputs)
    assert (
        feature_truth.main(
            ["--dir", str(inputs), "--results-dir", str(tmp_path), "--out", str(tmp_path / "out")]
        )
        == 0
    )
    assert (tmp_path / "out" / "feature-truth.md").is_file()
    assert snapshot(inputs) == before


def test_data_provenance_out_writes_there_and_leaves_dir_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WHAT: Runs data_provenance.main with --out on a tiny git-tracked fixture and checks provenance.md and
    measurements.json appear in --out while --dir is unchanged.
    WHY: Same reason: rendering must not touch the committed report.

    Args:
        tmp_path: pytest temporary folder.
        monkeypatch: pytest monkeypatch.
    Returns:
        Nothing.
    Example:
        --out tmp/out -> tmp/out/provenance.md and tmp/out/measurements.json exist
    """
    v1_dir = fixture_v1(tmp_path / "v1root")
    subprocess.run(["git", "init", "-q"], cwd=v1_dir, check=True, timeout=60)
    subprocess.run(["git", "add", "."], cwd=v1_dir, check=True, timeout=60)
    monkeypatch.setattr(
        data_provenance, "clone_exact", lambda branch, target, shallow: v1_dir.parent if shallow else tmp_path
    )
    item = {
        "path": "data/a.csv",
        "kind": "dataset",
        "source": [{"type": "code", "file": "requirements.txt", "line": 1, "contains": "requests"}],
        "licence": {"value": data_provenance.UNKNOWN_LICENCE},
        "personal_data": {"value": "no", "reason": "r"},
        "production_use": {"value": "not-for-production", "reason": "r"},
    }
    inputs = tmp_path / "in"
    write_files(inputs, {"provenance.yaml": yaml.safe_dump({"items": [item]}, allow_unicode=True)})
    before = snapshot(inputs)
    assert data_provenance.main(["--dir", str(inputs), "--out", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "provenance.md").is_file() and (
        tmp_path / "out" / "measurements.json"
    ).is_file()
    assert snapshot(inputs) == before


def test_screen_inventory_out_writes_there_and_leaves_dir_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WHAT: Runs screen_inventory.main with --out on a tiny frontend fixture and checks screens.md appears in
    --out while --dir (and the features file) are unchanged.
    WHY: Same reason: rendering must not touch the committed inventory.

    Args:
        tmp_path: pytest temporary folder.
        monkeypatch: pytest monkeypatch.
    Returns:
        Nothing.
    Example:
        --out tmp/out -> tmp/out/screens.md exists; tmp/in has only screens.yaml
    """
    v1_dir = fixture_v1(tmp_path / "v1root")
    monkeypatch.setattr(screen_inventory, "clone_exact", lambda branch, target, shallow: v1_dir.parent)
    action = {
        "name": "Open",
        "evidence": [
            {"type": "code", "file": "frontend/src/app/page.tsx", "line": 1, "contains": "export default"}
        ],
        "api_calls": [],
        "features": [],
    }
    page = {
        "route": "/",
        "file": "frontend/src/app/page.tsx",
        "purpose": "p",
        "user_actions": [action],
        "data_shown": [],
        "components": [],
        "linked_features": ["f1"],
    }
    screens = {
        "pages": [page],
        "unused_calls": [],
        "reviewed_non_literal_calls": [],
        "hard_coded_data": [],
        "must_keep": [],
        "rebuild_in_v2": [],
    }
    inputs = tmp_path / "in"
    write_files(inputs, {"screens.yaml": yaml.safe_dump(screens)})
    features_path = tmp_path / "features.yaml"
    features_path.write_text(yaml.safe_dump({"features": [feature_row(["x"])]}), encoding="utf-8")
    before = snapshot(inputs)
    assert (
        screen_inventory.main(
            ["--dir", str(inputs), "--features", str(features_path), "--out", str(tmp_path / "out")]
        )
        == 0
    )
    assert (tmp_path / "out" / "screens.md").is_file()
    assert snapshot(inputs) == before


def test_adr_000_out_writes_there_and_leaves_docs_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WHAT: Runs adr_000.main with --out on a tiny docs tree (one feature, no data or screens, every required
    module and service dropped with an owner reference) and checks the ADR appears in --out while the docs tree
    is byte-for-byte unchanged.
    WHY: Same reason: rendering must not overwrite the committed ADR.

    Args:
        tmp_path: pytest temporary folder.
        monkeypatch: pytest monkeypatch.
    Returns:
        Nothing.
    Example:
        --out tmp/out -> tmp/out/ADR-000-v1-carry-over.md exists; tmp/root/docs is unchanged
    """
    root = tmp_path / "root"
    summary = {"summary": {"passed": 1, "failed": 0, "error": 0, "skipped": 0}}
    owner = {"type": "owner-decision", "date": adr_000.OWNER_DECISION_DATE, "note": "n"}
    decisions = [
        {
            "item": "f1",
            "kind": "feature",
            "decision": "drop",
            "reason": "r",
            "v2_phase": None,
            "evidence": [owner],
        }
    ]
    for item, kind in adr_000.REQUIRED_MODULES_AND_SERVICES.items():
        decisions.append(
            {
                "item": item,
                "kind": kind,
                "decision": "drop",
                "reason": "r",
                "v2_phase": None,
                "evidence": [owner],
            }
        )
    screens: dict[str, list[object]] = {
        "pages": [],
        "unused_calls": [],
        "reviewed_non_literal_calls": [],
        "hard_coded_data": [],
        "must_keep": [],
        "rebuild_in_v2": [],
    }
    write_files(
        root / "docs",
        {
            "plan/phase-map.yaml": yaml.safe_dump(
                {"phases": [{"number": 1, "name": "Foundation"}, {"number": 3, "name": "Identity"}]}
            ),
            "phase0/feature-truth/features.yaml": yaml.safe_dump({"features": [feature_row(["x"])]}),
            "phase0/data-provenance/provenance.yaml": yaml.safe_dump({"items": []}),
            "phase0/screens/screens.yaml": yaml.safe_dump(screens),
            "phase0/v1-test-results/docker-with-db/results.json": json.dumps(summary),
            "phase0/v1-test-results/docker-with-db-health-risk/results.json": json.dumps(summary),
            "phase0/scorecard-flaws/results.md": "Positive control (test_positive_control): passed" + chr(10),
            "adr/decisions-000.yaml": yaml.safe_dump(
                {
                    "adr_date": "2026-10-22",
                    "adr_status": "Accepted",
                    "owner_decisions": [],
                    "decisions": decisions,
                }
            ),
        },
    )
    monkeypatch.setattr(adr_000, "clone_exact", lambda branch, target, shallow: tmp_path / "v1root")
    (tmp_path / "v1root" / "sentinel_flow_mvp").mkdir(parents=True)
    monkeypatch.chdir(root)
    before = snapshot(root / "docs")
    assert adr_000.main(["--out", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "ADR-000-v1-carry-over.md").is_file()
    assert snapshot(root / "docs") == before
