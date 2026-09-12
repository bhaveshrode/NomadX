import pytest
from decimal import Decimal
from datetime import datetime
from uuid import uuid4

from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.settlement_reconciliation.settlement import Settlement, SettlementEntry
from nomadx.src.domain.settlement_reconciliation.source_transaction import SourceTransaction
from nomadx.src.domain.settlement_reconciliation.reconciliation import ReconciliationStatus
from nomadx.src.domain.settlement_reconciliation.value_objects import (
    SettlementId,
    SettlementReference,
    SettlementAmount,
    SettlementCurrency,
    SettlementPeriod,
    SettlementLineReference,
    MerchantReference,
    SettlementIdempotencyKey
)
from nomadx.src.domain.settlement_reconciliation.reconciliation_engine import ReconciliationEngine


def _create_test_settlement(currency_code: str = "USDC", start_date: datetime = None, end_date: datetime = None) -> Settlement:
    start = start_date or datetime(2026, 9, 1)
    end = end_date or datetime(2026, 9, 5)
    return Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=OrganizationId(str(uuid4())),
        reference=SettlementReference(f"SET-{uuid4().hex[:6].upper()}"),
        merchant_reference=MerchantReference("MERCH-1"),
        period=SettlementPeriod(start, end),
        currency=SettlementCurrency(currency_code),
        idempotency_key=SettlementIdempotencyKey(str(uuid4()))
    ).value


def test_matching_policies_perfect_match():
    settlement = _create_test_settlement()
    entry = SettlementEntry(
        id=SettlementLineReference(f"LINE-{uuid4().hex[:8]}"),
        source_transaction_reference="TX-CONF-1",
        source_context_type="PAYMENT",
        gross_amount=SettlementAmount(Decimal("100.00")),
        fees_amount=SettlementAmount(Decimal("0.00")),
        adjustments_amount=SettlementAmount(Decimal("0.00")),
        net_amount=SettlementAmount(Decimal("100.00")),
        currency=SettlementCurrency(settlement.currency.code)
    )
    settlement.add_entry(entry)

    tx = SourceTransaction(
        id=str(uuid4()),
        organization_id=settlement.organization_id.value,
        transaction_reference="TX-CONF-1",
        amount=Decimal("100.00"),
        currency_code=settlement.currency.code,
        observed_at=datetime(2026, 9, 2),
        type="PAYMENT"
    )

    engine = ReconciliationEngine()
    result = engine.reconcile(settlement, [tx])

    assert result.status == ReconciliationStatus.MATCHED
    assert len(result.discrepancies) == 0


def test_matching_policies_missing_transaction():
    settlement = _create_test_settlement()
    entry = SettlementEntry(
        id=SettlementLineReference(f"LINE-{uuid4().hex[:8]}"),
        source_transaction_reference="TX-CONF-1",
        source_context_type="PAYMENT",
        gross_amount=SettlementAmount(Decimal("100.00")),
        fees_amount=SettlementAmount(Decimal("0.00")),
        adjustments_amount=SettlementAmount(Decimal("0.00")),
        net_amount=SettlementAmount(Decimal("100.00")),
        currency=SettlementCurrency(settlement.currency.code)
    )
    settlement.add_entry(entry)

    engine = ReconciliationEngine()
    result = engine.reconcile(settlement, [])

    assert result.status == ReconciliationStatus.DISCREPANCY
    assert len(result.discrepancies) == 1
    assert result.discrepancies[0].discrepancy_type == "MISSING_TRANSACTION"
    assert result.discrepancies[0].expected_amount.amount == Decimal("100.00")
    assert result.discrepancies[0].actual_amount.amount == Decimal("0.00")


def test_matching_policies_duplicate_transaction():
    settlement = _create_test_settlement()
    entry = SettlementEntry(
        id=SettlementLineReference(f"LINE-{uuid4().hex[:8]}"),
        source_transaction_reference="TX-CONF-1",
        source_context_type="PAYMENT",
        gross_amount=SettlementAmount(Decimal("100.00")),
        fees_amount=SettlementAmount(Decimal("0.00")),
        adjustments_amount=SettlementAmount(Decimal("0.00")),
        net_amount=SettlementAmount(Decimal("100.00")),
        currency=SettlementCurrency(settlement.currency.code)
    )
    settlement.add_entry(entry)

    tx1 = SourceTransaction(
        id=str(uuid4()),
        organization_id=settlement.organization_id.value,
        transaction_reference="TX-CONF-1",
        amount=Decimal("100.00"),
        currency_code=settlement.currency.code,
        observed_at=datetime(2026, 9, 2)
    )
    tx2 = SourceTransaction(
        id=str(uuid4()),
        organization_id=settlement.organization_id.value,
        transaction_reference="TX-CONF-1",
        amount=Decimal("100.00"),
        currency_code=settlement.currency.code,
        observed_at=datetime(2026, 9, 3)
    )

    engine = ReconciliationEngine()
    result = engine.reconcile(settlement, [tx1, tx2])

    assert result.status == ReconciliationStatus.DISCREPANCY
    assert len(result.discrepancies) == 1
    assert result.discrepancies[0].discrepancy_type == "DUPLICATE_TRANSACTION"
    assert result.discrepancies[0].actual_amount.amount == Decimal("200.00")


def test_matching_policies_currency_mismatch():
    settlement = _create_test_settlement()
    entry = SettlementEntry(
        id=SettlementLineReference(f"LINE-{uuid4().hex[:8]}"),
        source_transaction_reference="TX-CONF-1",
        source_context_type="PAYMENT",
        gross_amount=SettlementAmount(Decimal("100.00")),
        fees_amount=SettlementAmount(Decimal("0.00")),
        adjustments_amount=SettlementAmount(Decimal("0.00")),
        net_amount=SettlementAmount(Decimal("100.00")),
        currency=SettlementCurrency(settlement.currency.code)
    )
    settlement.add_entry(entry)

    tx = SourceTransaction(
        id=str(uuid4()),
        organization_id=settlement.organization_id.value,
        transaction_reference="TX-CONF-1",
        amount=Decimal("100.00"),
        currency_code="EUR", # Mismatch!
        observed_at=datetime(2026, 9, 2)
    )

    engine = ReconciliationEngine()
    result = engine.reconcile(settlement, [tx])

    assert result.status == ReconciliationStatus.DISCREPANCY
    assert result.discrepancies[0].discrepancy_type == "CURRENCY_MISMATCH"


def test_matching_policies_period_mismatch():
    settlement = _create_test_settlement()
    entry = SettlementEntry(
        id=SettlementLineReference(f"LINE-{uuid4().hex[:8]}"),
        source_transaction_reference="TX-CONF-1",
        source_context_type="PAYMENT",
        gross_amount=SettlementAmount(Decimal("100.00")),
        fees_amount=SettlementAmount(Decimal("0.00")),
        adjustments_amount=SettlementAmount(Decimal("0.00")),
        net_amount=SettlementAmount(Decimal("100.00")),
        currency=SettlementCurrency(settlement.currency.code)
    )
    settlement.add_entry(entry)

    tx = SourceTransaction(
        id=str(uuid4()),
        organization_id=settlement.organization_id.value,
        transaction_reference="TX-CONF-1",
        amount=Decimal("100.00"),
        currency_code=settlement.currency.code,
        observed_at=datetime(2026, 9, 10) # Outside Sept 1st-5th
    )

    engine = ReconciliationEngine()
    result = engine.reconcile(settlement, [tx])

    assert result.status == ReconciliationStatus.DISCREPANCY
    assert result.discrepancies[0].discrepancy_type == "PERIOD_MISMATCH"


def test_matching_policies_amount_mismatch_and_tolerance():
    engine = ReconciliationEngine()

    # Outside tolerance (diff = 0.10)
    settlement1 = _create_test_settlement()
    entry1 = SettlementEntry(
        id=SettlementLineReference(f"LINE-{uuid4().hex[:8]}"),
        source_transaction_reference="TX-CONF-1",
        source_context_type="PAYMENT",
        gross_amount=SettlementAmount(Decimal("100.00")),
        fees_amount=SettlementAmount(Decimal("0.00")),
        adjustments_amount=SettlementAmount(Decimal("0.00")),
        net_amount=SettlementAmount(Decimal("100.00")),
        currency=SettlementCurrency(settlement1.currency.code)
    )
    settlement1.add_entry(entry1)
    tx1 = SourceTransaction(
        id=str(uuid4()),
        organization_id=settlement1.organization_id.value,
        transaction_reference="TX-CONF-1",
        amount=Decimal("99.90"),
        currency_code=settlement1.currency.code,
        observed_at=datetime(2026, 9, 2)
    )
    res1 = engine.reconcile(settlement1, [tx1])
    assert res1.status == ReconciliationStatus.DISCREPANCY
    assert res1.discrepancies[0].discrepancy_type == "AMOUNT_MISMATCH"
    assert not res1.discrepancies[0].resolved

    # Within tolerance (diff = 0.02)
    settlement2 = _create_test_settlement()
    entry2 = SettlementEntry(
        id=SettlementLineReference(f"LINE-{uuid4().hex[:8]}"),
        source_transaction_reference="TX-CONF-2",
        source_context_type="PAYMENT",
        gross_amount=SettlementAmount(Decimal("100.00")),
        fees_amount=SettlementAmount(Decimal("0.00")),
        adjustments_amount=SettlementAmount(Decimal("0.00")),
        net_amount=SettlementAmount(Decimal("100.00")),
        currency=SettlementCurrency(settlement2.currency.code)
    )
    settlement2.add_entry(entry2)
    tx2 = SourceTransaction(
        id=str(uuid4()),
        organization_id=settlement2.organization_id.value,
        transaction_reference="TX-CONF-2",
        amount=Decimal("99.98"),
        currency_code=settlement2.currency.code,
        observed_at=datetime(2026, 9, 2)
    )
    res2 = engine.reconcile(settlement2, [tx2])
    assert res2.status == ReconciliationStatus.MATCHED
    assert len(res2.discrepancies) == 1
    assert res2.discrepancies[0].resolved
