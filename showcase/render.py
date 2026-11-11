# TERM: self-contained HTML - one file with its own CSS and no scripts, fonts or images fetched from anywhere.
# TERM: escaping - turning < > & " into harmless text so command output can never run as page code.
# TERM: exit gate - the pass/fail conditions G1-G6 that decide whether Phase 0 is finished.
"""Render the Phase 0 showcase Report as one self-contained HTML page. Every piece of text that came from a
command, a file or a person is HTML-escaped; the page contains no script and loads nothing from outside."""

from __future__ import annotations

import html

from showcase.phase0 import FAIL, NOT_RUN, PASS, UNAVAILABLE, CheckResult, Drift, Fact, Gate, Report, tail

# Text explaining why each check cannot be faked. Static prose only; the page's numbers all come from live runs.
HOW_VERIFIED = {
    "suite": "pytest runs in a fresh subprocess with its cache switched off. The counts are read from the junit file that run wrote, using the same "
    "reconciling parser as the v1 baseline (it refuses a file whose totals do not add up). The suite includes sabotage tests that feed broken "
    "data to every checker and expect each one to fail.",
    "feature_truth": "The checker clones the v1-final tag afresh, rediscovers every route, task, service, worker and page from the code, and re-checks "
    "every cited line and every cited test result. A citation that no longer holds, or an item with no feature, makes it exit non-zero.",
    "data_provenance": "The checker clones v1-final and the full history of v1-legacy, lists the git-tracked data files, and measures every size, "
    "SHA-256, row count and column list itself. Licences without fetched evidence are rejected; git claims are checked against git.",
    "screen_inventory": "The checker re-scans the frontend code of a fresh clone, matches every API call to the backend route list found in the code, "
    "proves 'unused' claims with import and identifier scans, and re-verifies every cited line.",
    "adr_000": "The checker reloads every Phase 0 artifact and requires exactly one decision for every feature, data item, screen action, module and service. "
    "It enforces the decision rules and resolves every evidence reference; the ADR's numbers are computed, not typed.",
    "scorecard_flaws": "The flaw tests run inside the saved Docker image against a fresh v1-final clone. The page re-reads the junit file itself: each "
    "flaw test must have failed with an AssertionError (an error or a pass is rejected), and the positive control must have passed.",
    "v1_rerun": "v1's own suite runs again now, in the saved image (checked by image ID; nothing is rebuilt), with the cache off. Its live counts are "
    "compared with the committed baseline, which is used only as the thing being compared against.",
}

STYLE = """
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#f6f7f9;color:#1c2330}
main{max-width:1000px;margin:0 auto;padding:24px 16px 64px}
h1{margin:0 0 4px}h2{margin-top:32px;border-bottom:1px solid #d5d9e0;padding-bottom:6px}
.banner{padding:18px 20px;border-radius:10px;color:#fff;font-size:1.4rem;font-weight:700}
.pass{background:#1a7f37}.fail{background:#c62828}.notrun{background:#b26a00}
.tag{display:inline-block;padding:2px 10px;border-radius:12px;color:#fff;font-weight:600;font-size:.85rem}
table{border-collapse:collapse;width:100%;background:#fff;margin:8px 0}
th,td{border:1px solid #d5d9e0;padding:6px 8px;text-align:left;vertical-align:top;font-size:.9rem}
th{background:#eef0f4}.red{color:#c62828;font-weight:600}
pre{background:#10151f;color:#e6e9ef;padding:12px;overflow:auto;border-radius:6px;font-size:.8rem;white-space:pre-wrap}
code{background:#eef0f4;padding:1px 4px;border-radius:4px}
"""


def esc(text: object) -> str:
    """WHAT: HTML-escapes any value (converted to text first), including quotes.
    WHY: Command output can contain <script> or other markup; it must show as text and never run.

    Args:
        text: any value.
    Returns:
        Escaped text.
    Example:
        "<script>alert(1)</script>" -> "&lt;script&gt;alert(1)&lt;/script&gt;"
    """
    return html.escape(str(text), quote=True)


def status_class(status: str) -> str:
    """WHAT: Maps a status (PASS, FAIL, NOT RUN) to its CSS colour class.
    WHY: Green for pass, red for fail, amber for not run.

    Args:
        status: the status text.
    Returns:
        "pass", "fail" or "notrun".
    Example:
        "NOT RUN" -> "notrun"
    """
    if status == PASS:
        return "pass"
    if status == NOT_RUN:
        return "notrun"
    return "fail"


def render_fact(fact: Fact) -> str:
    """WHAT: Renders one machine fact as a table row; an unavailable fact is shown in red.
    WHY: A fact that could not be read must be visible, not hidden.

    Args:
        fact: the fact.
    Returns:
        An HTML table row.
    Example:
        Fact("Docker", "unavailable", False) -> a row whose value has class "red"
    """
    css = "" if fact.available else ' class="red"'
    return f"<tr><th>{esc(fact.label)}</th><td{css}>{esc(fact.value)}</td></tr>"


def render_gate(gate: Gate) -> str:
    """WHAT: Renders one exit-gate row with a coloured status tag.
    WHY: The gate table is the verdict of the page.

    Args:
        gate: the gate.
    Returns:
        An HTML table row.
    Example:
        Gate("G5", ..., False, "NOT RUN", ...) -> a row with an amber NOT RUN tag
    """
    tag = f'<span class="tag {status_class(gate.status)}">{esc(gate.status)}</span>'
    return f"<tr><td>{esc(gate.gate_id)}</td><td>{esc(gate.claim)}</td><td>{tag}</td><td>{esc(gate.detail)}</td></tr>"


def render_drift(drift: Drift) -> str:
    """WHAT: Renders one drifted file: its name, the committed path and every differing line with its reason.
    WHY: A drift must show the exact lines that differ and why, never be summarised away.

    Args:
        drift: the drift.
    Returns:
        HTML.
    Example:
        a drift with 2 lines -> a table of 2 rows (line, reason)
    """
    rows = "".join(
        f"<tr><td><code>{esc(line)}</code></td><td>{esc(reason)}</td></tr>"
        for line, reason in drift.differing_lines
    )
    return (
        f'<p class="red">DRIFT: {esc(drift.file)} differs from the committed {esc(drift.committed_path)}</p>'
        f"<table><tr><th>differing line</th><th>why</th></tr>{rows}</table>"
    )


def render_check(check: CheckResult) -> str:
    """WHAT: Renders one check section: title and status, the claim, the command, exit code and seconds, the key
    numbers, notes, any drifts, and the last 30 lines of its real output.
    WHY: Each section lets a reader see what was claimed, what was run, and what really came back.

    Args:
        check: the check result.
    Returns:
        HTML.
    Example:
        a passing check -> a section with a green PASS tag and a <pre> of its output tail
    """
    tag = f'<span class="tag {status_class(check.status)}">{esc(check.status)}</span>'
    numbers = "".join(
        f"<tr><th>{esc(key)}</th><td>{esc(value)}</td></tr>" for key, value in check.numbers.items()
    )
    notes = "".join(f'<p class="red">{esc(note)}</p>' for note in check.notes)
    drifts = "".join(render_drift(drift) for drift in check.drifts)
    exit_text = "none (did not finish)" if check.exit_code is None else str(check.exit_code)
    return (
        f"<h2>{esc(check.title)} {tag}</h2><p><b>Claim:</b> {esc(check.claim)}</p>"
        f"<p><b>Command:</b> <code>{esc(check.command)}</code> &nbsp; <b>exit code:</b> {esc(exit_text)} &nbsp; <b>seconds:</b> {check.seconds:.1f}</p>"
        f"<table>{numbers}</table>{notes}{drifts}<pre>{esc(tail(check.output))}</pre>"
    )


def fact_value(facts: list[Fact], label: str) -> str:
    """WHAT: Returns the value of the fact with the given label, or "unavailable" if there is none.
    WHY: The banner shows the commit from the facts.

    Args:
        facts: the machine facts.
        label: the label to find.
    Returns:
        The value text.
    Example:
        [Fact("Commit", "b0461b6", True)], "Commit" -> "b0461b6"
    """
    for fact in facts:
        if fact.label == label:
            return fact.value
    return UNAVAILABLE


def render_report(report: Report) -> str:
    """WHAT: Renders the whole report as one self-contained HTML document: PASS/FAIL banner with commit, time and
    machine; the machine facts; the gate table; one section per check; and "How this was verified".
    WHY: The page is the showcase deliverable; it must be readable on its own and safe to open.

    Args:
        report: the Phase 0 report.
    Returns:
        The HTML text (no scripts, no external resources).
    Example:
        a passing report -> text starting "<!DOCTYPE html>" with a green banner "PHASE 0: PASS"
    """
    verdict = PASS if report.passed else FAIL
    commit = fact_value(report.facts, "Commit")
    banner = f'<div class="banner {status_class(verdict)}">PHASE 0: {esc(verdict)} &nbsp; commit {esc(commit)} &nbsp; started {esc(report.started)} UTC &nbsp; took {report.total_seconds:.0f} s</div>'
    facts = "".join(render_fact(fact) for fact in report.facts)
    gates = "".join(render_gate(gate) for gate in report.gates)
    sections = "".join(render_check(check) for check in report.checks)
    how = "".join(
        f"<p><b>{esc(check.title)}.</b> {esc(HOW_VERIFIED[check.check_id])}</p>" for check in report.checks
    )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>medoRa Phase 0 showcase</title>'
        f"<style>{STYLE}</style></head><body><main><h1>medoRa Phase 0 showcase</h1>{banner}"
        f"<h2>Machine</h2><table>{facts}</table><h2>Exit gate</h2><table><tr><th>gate</th><th>condition</th><th>status</th><th>detail</th></tr>{gates}</table>"
        f"{sections}<h2>How this was verified</h2>{how}</main></body></html>"
    )
