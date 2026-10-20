# TERM: flaw test - a test that states CORRECT behaviour and is expected to FAIL on v1 until v2 fixes it.
# TERM: positive control - a test that must PASS on v1; it proves the setup itself works.
# TERM: tenant - one pharmacy shop; scorecards must never mix shops' orders.
# TERM: paisa - one hundredth of a rupee; money must be exact to the paisa.
"""Flaw tests for v1's vendor scorecard (generate_financial_scorecard at v1-final, 3b121e1).

Each F-test states the correct behaviour as a property and is marked v1_flaw. They are designed to
fail on v1 with an AssertionError whose message says what v1 returned. No skip, no xfail.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from scorecard_adapter import OrderSpec, ScorecardResult, score_supplier

pytestmark = pytest.mark.v1_flaw

AS_OF = date(2026, 3, 1)
SHOP_A = "shop-A"
SHOP_B = "shop-B"
PRICE = Decimal("10.00")
MRP = Decimal("15.00")


def good_order(tenant_id: str = SHOP_A, days_before: int = 20) -> OrderSpec:
    """WHAT: Builds a fully delivered, on-time order that expected delivery `days_before` days before AS_OF.
    WHY: Most flaw tests need a "good" order next to the one that exposes the flaw.

    Args:
        tenant_id: the shop that placed the order.
        days_before: how long before AS_OF the delivery was expected.
    Returns:
        An OrderSpec delivered one day early, in full.
    Example:
        good_order("shop-A", 20) -> expected 2026-02-09, delivered 2026-02-08, 10 of 10 units
    """
    expected = AS_OF - timedelta(days=days_before)
    return OrderSpec(tenant_id, 10, 10, PRICE, MRP, expected - timedelta(days=5), expected,
                     expected - timedelta(days=1), "DELIVERED")


def failed_order(tenant_id: str = SHOP_A, days_before: int = 20) -> OrderSpec:
    """WHAT: Builds a clearly failed order: nothing delivered, delivery expected `days_before` days before AS_OF.
    WHY: Several flaw tests need a real, due failure.

    Args:
        tenant_id: the shop that placed the order.
        days_before: how long before AS_OF the delivery was expected.
    Returns:
        An OrderSpec with 0 of 10 units delivered and status FAILED.
    Example:
        failed_order("shop-B", 7) -> expected 2026-02-22, delivered 0 of 10
    """
    expected = AS_OF - timedelta(days=days_before)
    return OrderSpec(tenant_id, 10, 0, PRICE, MRP, expected - timedelta(days=5), expected, None, "FAILED")


def test_positive_control() -> None:
    """WHAT: Five on-time, full deliveries must give TRUSTED with no failures.
    WHY: If this fails the test set-up is broken, so the F-tests' failures would prove nothing.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        5 good orders -> risk_level "TRUSTED", recent_failures 0 (passes on v1)
    """
    result = score_supplier([good_order() for _ in range(5)], SHOP_A, AS_OF)
    assert result.risk_level == "TRUSTED" and result.recent_failures == 0, f"control: got {result}"


def test_f1_no_orders_is_not_trusted() -> None:
    """WHAT: Flaw F1 (v1 lines 30-39): a supplier with no orders at all must not be rated TRUSTED.
    WHY: A pharmacy would otherwise treat an unknown distributor as the safest one.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        no orders -> v1 returns TRUSTED with success rate 100; correct: not TRUSTED
    """
    result = score_supplier([], SHOP_A, AS_OF)
    assert result.risk_level != "TRUSTED", (
        f"v1 returned risk_level={result.risk_level}, success_rate={result.delivery_success_rate} for a supplier "
        "with zero orders; correct: an unrated supplier is not TRUSTED")


def test_f2_score_reflects_failure_rate_not_failure_count() -> None:
    """WHAT: Flaw F2 (v1 line 73): trust is 100 - 15 x failures, not a rate. A supplier failing 2 of 2
    orders must score strictly lower than one failing 7 of 200.
    WHY: A big, mostly reliable distributor would otherwise rank below a tiny one that always fails.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        v1: 2 of 2 failed -> trust 70; 7 of 200 failed -> trust 0; correct: first < second
    """
    always_failing = score_supplier([failed_order() for _ in range(2)], SHOP_A, AS_OF)
    mostly_good = score_supplier([failed_order() for _ in range(7)] + [good_order() for _ in range(193)], SHOP_A, AS_OF)
    assert always_failing.trust_score < mostly_good.trust_score, (
        f"v1 scored 2-of-2 failing = {always_failing.trust_score} and 7-of-200 failing = {mostly_good.trust_score}; "
        "correct: failing every order scores lower than failing 3.5% of orders")


def test_f3_pending_orders_not_yet_due_are_not_failures() -> None:
    """WHAT: Flaw F3 (v1 lines 55-59): 5 on-time deliveries plus 1 PENDING order whose delivery is not due
    yet (relative to AS_OF) must show recent_failures == 0.
    WHY: A pharmacy placing a fresh order would otherwise see its distributor's score drop at once.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        v1: delivered_qty 0 < ordered 10 -> counted as a failure (recent_failures 1); correct: 0
    """
    not_due = OrderSpec(SHOP_A, 10, 0, PRICE, MRP, AS_OF - timedelta(days=1), AS_OF + timedelta(days=9), None, "PENDING")
    result = score_supplier([good_order() for _ in range(5)] + [not_due], SHOP_A, AS_OF)
    assert result.recent_failures == 0, (
        f"v1 returned recent_failures={result.recent_failures} with 5 good orders and 1 PENDING order due on "
        f"{not_due.expected_delivery_date}; correct: 0")


def test_f4_scorecard_does_not_mix_tenants() -> None:
    """WHAT: Flaw F4 (v1 lines 26-28): shop A has only good orders with a supplier; shop B's orders with the
    same supplier all fail. Shop A's scorecard must show recent_failures == 0.
    WHY: Showing one pharmacy another pharmacy's order history is a data leak and a wrong score.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        v1: no tenant filter, so A sees B's 3 failures; correct: 0
    """
    orders = [good_order(SHOP_A) for _ in range(3)] + [failed_order(SHOP_B) for _ in range(3)]
    result = score_supplier(orders, SHOP_A, AS_OF)
    assert result.recent_failures == 0, (
        f"v1 returned recent_failures={result.recent_failures} to viewer {SHOP_A}, who has only good orders; "
        f"the failures belong to {SHOP_B}; correct: 0")


def test_f5_money_is_exact_decimal() -> None:
    """WHAT: Flaw F5 (v1 lines 44 and 62): the loss is added up as a float. For one short order of
    1,000,001 units with a margin of Rs 99,999,999.99 the exact loss is 100000099989999.99; the money
    must also not be carried as a float.
    WHY: Money must be exact to the paisa. NOTE: the wrong value only appears at about Rs 1e14; in 200,000
    random realistic trials (margin up to Rs 500, up to 40 orders) float and Decimal agreed, so this is a
    type flaw whose wrong value is demonstrated only at an extreme but schema-legal input.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        v1 returns the float 100000099989999.98 (one paisa off); correct: 100000099989999.99 as Decimal
    """
    order = OrderSpec(SHOP_A, 1_000_001, 0, Decimal("0.00"), Decimal("99999999.99"),
                      AS_OF - timedelta(days=30), AS_OF - timedelta(days=20), None, "FAILED")
    result = score_supplier([order], SHOP_A, AS_OF)
    exact = Decimal("1000001") * Decimal("99999999.99")
    assert result.financial_loss == exact and result.financial_loss_python_type != "float", (
        f"v1 returned {result.financial_loss} as {result.financial_loss_python_type}; correct: exact Decimal {exact}")


def test_f6_old_failures_age_out() -> None:
    """WHAT: Flaw F6 (v1 lines 45-65): two suppliers identical except that one failure is dated 3 years
    before AS_OF versus 7 days before. The old-failure supplier must score strictly higher.
    WHY: A distributor that fixed its problems years ago should not be punished like one failing this week.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        v1 scores both 85 (no time window); correct: old-failure score > recent-failure score
    """
    good_orders = [good_order() for _ in range(4)]
    old_failure = score_supplier(good_orders + [failed_order(days_before=3 * 365)], SHOP_A, AS_OF)
    recent_failure = score_supplier(good_orders + [failed_order(days_before=7)], SHOP_A, AS_OF)
    assert old_failure.trust_score > recent_failure.trust_score, (
        f"v1 scored the 3-year-old failure {old_failure.trust_score} and the 7-day-old failure "
        f"{recent_failure.trust_score}; correct: the old failure scores strictly higher")


def test_f7_loss_is_never_negative() -> None:
    """WHAT: Flaw F7 (v1 lines 61-62): when cost_price is above expected_mrp and the delivery is short,
    financial_loss must not be negative.
    WHY: A short delivery can never make a distributor look like it earned the pharmacy money.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        cost 120, MRP 100, 10 ordered, 5 delivered -> v1 loss -100.0; correct: >= 0
    """
    order = OrderSpec(SHOP_A, 10, 5, Decimal("120.00"), Decimal("100.00"), AS_OF - timedelta(days=30),
                      AS_OF - timedelta(days=20), AS_OF - timedelta(days=20), "PARTIAL")
    result = score_supplier([order], SHOP_A, AS_OF)
    assert result.financial_loss >= 0, f"v1 returned financial_loss={result.financial_loss}; correct: >= 0"


def test_f8_success_rate_is_not_truncated() -> None:
    """WHAT: Flaw F8 (v1 line 70): 199 good orders out of 200 is 99.5 percent. int() cuts it to 99.
    The rate must not be 99 (rounding to the nearest whole percent gives 100).
    WHY: Truncation always rounds against the supplier, so near-perfect suppliers never reach 100.

    Args:
        none.
    Returns:
        Nothing.
    Example:
        v1 returns delivery_success_rate 99; correct: not 99 (100 when rounded to nearest)
    """
    result: ScorecardResult = score_supplier([good_order() for _ in range(199)] + [failed_order()], SHOP_A, AS_OF)
    assert result.delivery_success_rate != 99, (
        f"v1 returned delivery_success_rate={result.delivery_success_rate} for 199 good orders of 200 "
        "(99.5 percent, truncated by int()); correct: not 99")
