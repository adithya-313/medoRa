# TERM: adapter - a thin translator between the tests' own data types and one implementation (here: v1).
# TERM: OrderSpec - one purchase order described in plain Python types (money as Decimal).
# TERM: in-memory SQLite - a throw-away database that lives only in RAM, rebuilt for every call.
# TERM: tenant - one pharmacy shop; v1 is meant to keep shops' data apart.
"""v1 adapter for the vendor-scorecard flaw tests.

score_supplier() is the only function the flaw tests call. It builds a fresh in-memory SQLite database
with v1's own models, inserts the given purchase orders, calls v1's generate_financial_scorecard and
converts the response. The interface does not mention v1, so a v2 adapter can later replace this file.

Needs v1 (sentinel_flow_mvp at tag v1-final) on PYTHONPATH. Never collected by the normal medoRa suite.
"""
from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.infrastructure.models import Base, PurchaseOrderModel, SupplierModel, Tenant
from app.services.supplier_scorecard_service import generate_financial_scorecard

SUPPLIER_ID = "SUPP-FLAW-1"
SKU_ID = "SKU-FLAW-1"


# The two SQLite shims below are copied from v1-final: sentinel_flow_mvp/tests/integration/
# test_overdue_po_notifications.py lines 13-30. SQLite has no JSONB or ARRAY type; v1's own tests
# compile both as JSON so that Base.metadata.create_all works on SQLite.
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_: object, compiler: object, **kw: object) -> str:
    """WHAT: Tells SQLAlchemy to create JSONB columns as JSON when the database is SQLite.
    WHY: v1's models use PostgreSQL types that SQLite does not know.

    Args:
        type_: the column type.
        compiler: the SQL compiler.
        **kw: extra compiler arguments.
    Returns:
        The SQL type name "JSON".
    Example:
        a JSONB column on SQLite -> "JSON"
    """
    return "JSON"


@compiles(ARRAY, "sqlite")
def compile_array_sqlite(type_: object, compiler: object, **kw: object) -> str:
    """WHAT: Tells SQLAlchemy to create ARRAY columns as JSON when the database is SQLite.
    WHY: v1's medication table has array columns that SQLite does not know.

    Args:
        type_: the column type.
        compiler: the SQL compiler.
        **kw: extra compiler arguments.
    Returns:
        The SQL type name "JSON".
    Example:
        an ARRAY(Text) column on SQLite -> "JSON"
    """
    return "JSON"


@dataclasses.dataclass(frozen=True)
class OrderSpec:
    """One purchase order. Money is Decimal; dates are plain dates."""

    tenant_id: str
    ordered_qty: int
    delivered_qty: int
    cost_price: Decimal
    expected_mrp: Decimal
    order_date: date
    expected_delivery_date: date
    actual_delivery_date: date | None
    status: str


@dataclasses.dataclass(frozen=True)
class ScorecardResult:
    """What a scorecard implementation returned, with money as Decimal.

    financial_loss_python_type is the Python type name the implementation returned before conversion
    ("float" for v1), so tests can check that money is not carried as a float.
    """

    supplier_id: str
    name: str
    financial_loss: Decimal
    financial_loss_python_type: str
    delivery_success_rate: int
    trust_score: int
    risk_level: str
    recent_failures: int


def validate_order(order: OrderSpec) -> None:
    """WHAT: Checks one OrderSpec has sane types and values.
    WHY: A typo in a flaw test must stop with a clear message, not produce a confusing v1 failure.

    Args:
        order: the order to check.
    Returns:
        Nothing.
    Raises:
        ValueError: for an empty tenant id, negative quantities, non-Decimal money or a missing status.
    Example:
        OrderSpec(ordered_qty=-1, ...) -> ValueError("ordered_qty and delivered_qty must be >= 0")
    """
    if not order.tenant_id or not order.status:
        raise ValueError("tenant_id and status must not be empty")
    if order.ordered_qty < 0 or order.delivered_qty < 0:
        raise ValueError("ordered_qty and delivered_qty must be >= 0")
    if not isinstance(order.cost_price, Decimal) or not isinstance(order.expected_mrp, Decimal):
        raise ValueError("cost_price and expected_mrp must be Decimal")


def build_database(orders: list[OrderSpec], viewer_tenant: str) -> Session:
    """WHAT: Creates a fresh in-memory SQLite database with v1's tables and inserts one supplier, one
    medication, one tenant row per tenant that appears (including the viewer) and every order.
    WHY: v1's function reads from a database session, so the test data has to live in one.

    Args:
        orders: the purchase orders to insert (all for SUPPLIER_ID and SKU_ID).
        viewer_tenant: a tenant id that must exist as a row.
    Returns:
        An open session on that database (the caller closes it).
    Side effects:
        Creates and fills a database that exists only in memory.
    Example:
        3 orders from tenants "A" and "B", viewer "A" -> a session whose purchase_orders table has 3 rows
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    tenant_ids = sorted({order.tenant_id for order in orders} | {viewer_tenant})
    for tenant_id in tenant_ids:
        session.add(Tenant(tenant_id=tenant_id, name=f"Shop {tenant_id}"))
    session.add(SupplierModel(supplier_id=SUPPLIER_ID, system_origin="TEST", industry_vertical="PHARMA",
                              industry_specific_metadata={"name": "Flaw Test Pharma"}))
    # SQLite cannot bind a Python list to v1's ARRAY columns, so the medication row is inserted with raw
    # SQL, exactly as v1's own test does (test_overdue_po_notifications.py, the "INSERT INTO medications" text).
    session.execute(text(
        "INSERT INTO medications (sku_id, apis, therapeutic_classes, cdsco_schedule, is_approved_by_curator) "
        f"VALUES ('{SKU_ID}', '[]', '[]', 'OTC', 1)"))
    session.commit()
    for position, order in enumerate(orders, start=1):
        session.add(PurchaseOrderModel(
            po_id=f"PO-{position}", tenant_id=order.tenant_id, supplier_id=SUPPLIER_ID, sku_id=SKU_ID,
            ordered_qty=order.ordered_qty, delivered_qty=order.delivered_qty, cost_price=order.cost_price,
            expected_mrp=order.expected_mrp, order_date=order.order_date,
            expected_delivery_date=order.expected_delivery_date,
            actual_delivery_date=order.actual_delivery_date, status=order.status))
    session.commit()
    return session


def score_supplier(orders: list[OrderSpec], viewer_tenant: str, as_of: date) -> ScorecardResult:
    """WHAT: Scores the supplier SUPP-FLAW-1 with v1's generate_financial_scorecard and returns the
    result with money converted to Decimal (through the float's shortest text form).
    WHY: One stable function lets the same flaw tests run later against a v2 implementation.

    v1 has no tenant parameter and no as_of parameter, so this adapter CANNOT pass viewer_tenant or
    as_of. They are accepted (and viewer_tenant is validated and given a tenant row) only to keep the
    interface independent of v1. The adapter adds no filtering and no date logic: whatever v1 does with
    all orders of all tenants is what comes back. That is what exposes flaws F3, F4 and F6.

    Args:
        orders: the purchase orders for the supplier, from any tenants.
        viewer_tenant: the shop asking for the scorecard (ignored by v1).
        as_of: the date "today" for the scorecard (ignored by v1).
    Returns:
        ScorecardResult.
    Raises:
        ValueError: for invalid orders or an empty viewer tenant.
    Example:
        5 on-time full deliveries -> ScorecardResult(trust_score=100, risk_level="TRUSTED", recent_failures=0, ...)
    """
    if not viewer_tenant:
        raise ValueError("viewer_tenant must not be empty")
    for order in orders:
        validate_order(order)
    session = build_database(orders, viewer_tenant)
    try:
        response = generate_financial_scorecard(session, SUPPLIER_ID)
    finally:
        session.close()
    raw_loss = response.financial_loss_rupees
    return ScorecardResult(
        supplier_id=response.supplier_id, name=response.name, financial_loss=Decimal(str(raw_loss)),
        financial_loss_python_type=type(raw_loss).__name__, delivery_success_rate=response.delivery_success_rate,
        trust_score=response.trust_score, risk_level=response.risk_level, recent_failures=response.recent_failures)
