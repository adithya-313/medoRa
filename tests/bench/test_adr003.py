"""Unit tests for the ADR-003 harness decision rule, threshold rule, and score formula."""

from __future__ import annotations

import pytest

from bench.adr003.run_benchmark import decide_winner, measurement_score, unit_threshold

pytestmark = pytest.mark.unit


def _candidate(
    name: str,
    *,
    h1: bool = True,
    h2: bool = True,
    h3: bool = True,
    h4: bool = True,
    h5: bool = True,
    m1: float | None = 10.0,
    m4_native_windows: str = "no",
) -> dict[str, object]:
    """Build a fake candidate result dict for decision-rule tests."""
    m4 = {"native_windows": m4_native_windows, "exit_code": 0 if m4_native_windows == "yes" else 1}
    return {
        "H1": h1,
        "H2": h2,
        "H3": h3,
        "H4": h4,
        "H5": h5,
        "M1": m1,
        "M4": m4,
    }


def test_decision_rule_disqualifies_failed_hard_rules() -> None:
    """A tool with lower M1 but a failed hard rule must not win."""
    candidates = {
        "fast-but-disqualified": _candidate("fast-but-disqualified", h3=False, m1=1.0),
        "valid-slower": _candidate("valid-slower", m1=10.0),
    }
    winner, detail = decide_winner(candidates)
    assert winner == "valid-slower"
    assert detail["m1"] == 10.0


def test_decision_rule_lowest_m1_wins() -> None:
    """Among H1-H5 passers, the lowest M1 wins."""
    candidates = {
        "slow": _candidate("slow", m1=50.0),
        "fast": _candidate("fast", m1=10.0),
        "medium": _candidate("medium", m1=30.0),
    }
    winner, _ = decide_winner(candidates)
    assert winner == "fast"


def test_decision_rule_twenty_percent_tie_break_prefers_m4_yes() -> None:
    """Within 20% of the fastest M1, prefer M4 = yes, then lowest M1."""
    # medium (M1=12, M4=yes) and fast (M1=10, M4=no) are within 20% of 10.
    # medium is within 20%? (12-10)/10 = 0.20 -> yes. Tie pool = {fast, medium}.
    # Prefer M4=yes -> medium wins despite higher M1.
    candidates = {
        "fast-no-m4": _candidate("fast-no-m4", m1=10.0, m4_native_windows="no"),
        "medium-yes-m4": _candidate("medium-yes-m4", m1=12.0, m4_native_windows="yes"),
        "slow": _candidate("slow", m1=100.0),
    }
    winner, detail = decide_winner(candidates)
    assert winner == "medium-yes-m4"
    assert "M4" in detail["reason"] or detail["m1"] == 12.0


def test_decision_rule_tie_without_m4_yes_picks_lowest_m1() -> None:
    """Within 20% tie with no M4=yes, the lowest M1 wins."""
    candidates = {
        "a": _candidate("a", m1=10.0, m4_native_windows="no"),
        "b": _candidate("b", m1=11.0, m4_native_windows="no"),
    }
    winner, _ = decide_winner(candidates)
    assert winner == "a"


def test_decision_rule_outside_20_percent_is_not_a_tie() -> None:
    """A candidate more than 20% slower than fastest does not enter the tie pool."""
    candidates = {
        "fast": _candidate("fast", m1=10.0, m4_native_windows="no"),
        "slow": _candidate("slow", m1=20.0, m4_native_windows="yes"),  # 100% slower
    }
    winner, _ = decide_winner(candidates)
    assert winner == "fast"


def test_decision_rule_no_eligible_candidate() -> None:
    """When no candidate passes H1-H5, there is no winner."""
    candidates = {
        "a": _candidate("a", h1=False),
        "b": _candidate("b", h5=False),
    }
    winner, detail = decide_winner(candidates)
    assert winner is None
    assert "H1-H5" in detail["reason"]


@pytest.mark.parametrize(
    "score,expected",
    [
        (87.3, 85.0),
        (70.0, 70.0),
        (74.9, 70.0),
        (75.0, 75.0),
        (99.9, 95.0),
        (100.0, 100.0),
    ],
)
def test_unit_threshold_rounds_down_to_nearest_5(score: float, expected: float) -> None:
    """The unit threshold rounds M3 down to the nearest 5, never below 70."""
    result = unit_threshold(score)
    assert result["action"] == "set"
    assert result["score"] == expected


def test_unit_threshold_below_70_stops() -> None:
    """An M3 below 70 produces a STOP signal with no threshold."""
    result = unit_threshold(69.9)
    assert result["action"] == "stop"
    assert result["score"] is None
    assert "STOP" in result["detail"]

    result = unit_threshold(None)
    assert result["action"] == "stop"


def test_measurement_score_counts_timeouts_as_killed() -> None:
    """The mutation score counts timeouts as killed and states it."""
    score, detail = measurement_score(killed=7, survived=2, timeouts=1)
    # killed+timeouts = 8, survived = 2, score = 8 / (7+2) = 88.88...
    assert score == pytest.approx(88.8888, rel=1e-3)
    assert "timeouts counted as killed" in detail
    assert "timeouts=1" in detail


def test_measurement_score_no_mutants() -> None:
    """With no mutants evaluated, the score is zero and states it."""
    score, detail = measurement_score(killed=0, survived=0, timeouts=0)
    assert score == 0.0
    assert "No mutants evaluated" in detail
