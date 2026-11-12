import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS = ROOT / "bench" / "adr003" / "results" / "results.json"
ADR = ROOT / "docs" / "adr" / "ADR-003-mutation-tool.md"


def run(cmd: list[str]) -> tuple[int | None, str]:
    """Run a command and return (exit_code, first_output_line)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        out = (proc.stdout or proc.stderr).strip()
        detail = out.splitlines()[0][:200] if out else ""
        return proc.returncode, detail
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:200]


def native(tool: str) -> dict[str, Any]:
    """Record a native Windows attempt for M4 after container benchmarking."""
    if tool == "mutmut":
        cmd = ["mutmut", "--version"]
    elif tool == "cosmic-ray":
        cmd = ["cosmic-ray", "--version"]
    else:
        cmd = [sys.executable, "-m", "pytest", "--gremlins", "--help"]
    code, detail = run(cmd)
    if code is None:
        return {
            "native_windows": "no",
            "exit_code": None,
            "note": f"native Windows attempt failed to run: {detail}",
        }
    return {
        "native_windows": "yes" if code == 0 else "no",
        "exit_code": code,
        "note": f"native Windows host attempt exited {code}: {detail}",
    }


def fmt(value: Any) -> str:
    """Format metric values for markdown tables."""
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def main() -> None:
    """Merge host M4 attempts into results.json and render ADR-003 markdown."""
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    for tool in data["candidates"]:
        data["candidates"][tool]["M4"] = native(tool)
    RESULTS.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    lines: list[str] = []
    lines.append("# ADR-003: Mutation Testing Tool")
    lines.append("")
    lines.append("## Status")
    lines.append("Accepted (no eligible winner)")
    lines.append("")
    lines.append("## Context")
    lines.append(
        "Phase 1 Round 7a benchmarks mutation-testing candidates for `medoRa` on "
        "Windows-hosted Docker against fixed targets, per `docs/adr/ADR-003-criteria.md`."
    )
    lines.append("")
    lines.append("## Candidates")
    for tool, version in data["tool_versions"].items():
        lines.append(f"- `{tool}` {version} ({data['licence_urls'][tool]})")
    lines.append("")
    lines.append("## Targets")
    lines.append("- Source: " + ", ".join(f"`{x}`" for x in data["target_files"]))
    lines.append("- Tests: " + ", ".join(f"`{x}`" for x in data["unit_tests"]))
    lines.append(
        f"- Execution: `{data['image']}` container (`{data['container']}`), repo "
        "mounted at `/repo`, `uv sync --group dev --group adr003`."
    )
    lines.append("")
    lines.append("## Hard Rules (H1-H5) and Metrics")
    lines.append("")
    lines.append(
        "| Candidate | H1 | H2 | H3 | H4 | H5 | M1 median (s) | M2 mutants | M3 (%) | M4 native Windows |"
    )
    lines.append("|---|:---:|:---:|:---:|:---:|:---:|---:|---:|---:|---|")
    for name in ["mutmut", "cosmic-ray", "pytest-gremlins"]:
        cand = data["candidates"][name]
        m4 = cand.get("M4") or {}
        m4s = f"{m4.get('native_windows')} (exit {m4.get('exit_code')})"
        lines.append(
            f"| {name} | {'PASS' if cand['H1'] else 'FAIL'} | "
            f"{'PASS' if cand['H2'] else 'FAIL'} | {'PASS' if cand['H3'] else 'FAIL'} | "
            f"{'PASS' if cand['H4'] else 'FAIL'} | {'PASS' if cand['H5'] else 'FAIL'} | "
            f"{fmt(cand.get('M1'))} | {fmt(cand.get('M2'))} | {fmt(cand.get('M3'))} | {m4s} |"
        )
    lines.append("")
    lines.append("## Candidate Details")
    for name in ["mutmut", "cosmic-ray", "pytest-gremlins"]:
        cand = data["candidates"][name]
        lines.append(f"### {name}")
        lines.append(f"- H1: {cand.get('H1_detail')}")
        lines.append(f"- H2: {cand.get('H2_detail')}")
        lines.append(f"- H3: {cand.get('H3_detail')}")
        lines.append(
            f"- H4: {cand.get('H4_detail')} (full={cand.get('demo_full_survivors')}, "
            f"weakened={cand.get('demo_weakened_survivors')})"
        )
        if cand.get("M3_detail"):
            lines.append(f"- M3: {cand.get('M3_detail')}")
        m4 = cand.get("M4") or {}
        lines.append(f"- M4: {m4.get('native_windows')} exit={m4.get('exit_code')} note={m4.get('note')}")
        lines.append("")
    lines.append("## Decision")
    decision = data["decision"]
    lines.append(f"- Winner: `{decision.get('winner')}`")
    lines.append(f"- Rule outcome: {decision.get('decision_detail', {}).get('reason')}")
    threshold = decision.get("unit_threshold", {})
    lines.append(
        f"- Unit threshold: action={threshold.get('action')} score={threshold.get('score')} "
        f"({threshold.get('detail')})"
    )
    lines.append("")
    lines.append("## Consequences")
    lines.append(
        "- No mutation-testing tool selected for enforced CI/unit thresholds in this round "
        "because no candidate passed H1-H5 on the fixed target under the pinned harness."
    )
    lines.append("- `adr003` remains an opt-in dependency group; normal `uv sync` does not install it.")
    lines.append(
        "- A follow-up round must address result parsing/timeout reporting and demo "
        "evaluation so H3/H4 can be measured reliably before adopting any tool or threshold."
    )
    lines.append("")
    lines.append("## Limits")
    lines.append(
        "- Single-machine, single-run benchmark on Windows host with Docker Desktop; "
        "timings reflect `python:3.12` container execution and host load."
    )
    lines.append(
        f"- Harness install/sync result: `install_ok={data.get('install_ok')}`; "
        "in-container uv version observed during run was 0.13.7 while host baseline is "
        "uv 0.11.8 (per task context)."
    )
    lines.append(
        "- H3 failed for all candidates because killed/survived/timeout counts were not "
        "parseable or not fully reported by the harness/tool combination used "
        "(pytest-gremlins lacks machine-readable timeout counts in this harness)."
    )
    lines.append(
        "- H4 failed because demo survivor counts were unparseable for all candidates; "
        "weakened-vs-full assertion discrimination was not established by this run."
    )
    lines.append(
        "- M4 native Windows attempts were performed on the host after container run; "
        "tools not installed natively reported as unavailable with recorded exit/detail "
        "(see per-candidate M4)."
    )
    lines.append(
        "- Results are from harness-written `bench/adr003/results/results.json`; no metrics "
        "were typed by hand."
    )
    lines.append("- Demo tests remain excluded from normal collection via `pytest.ini` `norecursedirs`.")
    lines.append("")
    ADR.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("updated", RESULTS)
    print("wrote", ADR)


if __name__ == "__main__":
    main()
