# TERM: showcase - a live demonstration that re-runs every Phase 0 check and reports the result.
# TERM: exit gate - the pass/fail conditions G1-G6 that decide whether Phase 0 is finished.
"""Command line: `python -m showcase run --phase 0` runs every Phase 0 check live, writes
showcase/out/phase-0.html and exits 0 only if every gate passed."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from showcase.phase0 import exit_code_for, run_phase0
from showcase.render import render_report

SUPPORTED_PHASES = [0]
OUTPUT_PATH = Path("showcase/out/phase-0.html")


def main(argv: list[str]) -> int:
    """WHAT: Parses the command line, runs the showcase for the requested phase from the current folder (the repo
    root), writes the HTML page, prints the gate table and returns the exit code.
    WHY: One command gives a reviewer the whole verdict.

    Args:
        argv: command-line arguments.
    Returns:
        0 when every gate passed, 1 when any gate failed, 2 for a bad request.
    Side effects:
        Writes showcase/out/phase-0.html and runs many subprocesses.
    Example:
        main(["run", "--phase", "0"]) -> 0 when G1-G6 all pass
    """
    parser = argparse.ArgumentParser(
        prog="showcase", description="Re-run and show the checks of a medoRa phase."
    )
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--phase", type=int, required=True)
    args = parser.parse_args(argv)
    if args.phase not in SUPPORTED_PHASES:
        print(f"phase {args.phase} is not supported yet (supported: {SUPPORTED_PHASES})")
        return 2
    repo_root = Path.cwd()
    if not (repo_root / "tools" / "phase0").is_dir():
        print("run this from the medoRa repo root (tools/phase0 not found)")
        return 2
    report = run_phase0(repo_root)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render_report(report), encoding="utf-8")
    for gate in report.gates:
        print(f"{gate.gate_id} {gate.status:8} {gate.claim}")
    print(
        f"PHASE 0: {'PASS' if report.passed else 'FAIL'} in {report.total_seconds:.0f} s; page: {OUTPUT_PATH}"
    )
    return exit_code_for(report.gates)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
