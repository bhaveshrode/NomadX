import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.settlement_reconciliation import (
    Settlement,
    SettlementStatus,
    SettlementEntry,
    SettlementId,
    SettlementReference,
    SettlementBatchReference,
    SettlementCurrency,
    SettlementAmount,
    SettlementPeriod,
    SettlementLineReference,
    MerchantReference,
    SettlementIdempotencyKey,
    ReconciliationStatus,
    ReconciliationResult,
    ReconciliationDiscrepancy,
    ReconciliationDifference,
    AdjustmentType,
    SettlementAdjustment,
    SettlementCreated,
    SettlementOpened,
    SettlementEntryAdded,
    SettlementReconciliationStarted,
    SettlementReconciled,
    SettlementDiscrepancyDetected,
    SettlementCompleted,
    SettlementCancelled,
    SettlementAdjustmentApplied
)

class ControllableClock(Clock):
    _time = datetime(2026, 9, 10, tzinfo=timezone.utc)

    @classmethod
    def now(cls) -> datetime:
        return cls._time

    @classmethod
    def set_time(cls, new_time: datetime):
        cls._time = new_time


@pytest.fixture(autouse=True)
def patch_clock(monkeypatch):
    monkeypatch.setattr(Clock, "now", ControllableClock.now)


@pytest.fixture
def base_setup():
    org_id = OrganizationId(str(uuid4()))
    ref = SettlementReference("SET-2026-000001")
    merchant_ref = MerchantReference("MERCH-ORG-A")
    period = SettlementPeriod(
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 7, tzinfo=timezone.utc)
    )
    currency = SettlementCurrency("USDC")
    idem_key = SettlementIdempotencyKey("idem-batch-12345")
    
    return {
        "org_id": org_id,
        "ref": ref,
        "merchant_ref": merchant_ref,
        "period": period,
        "currency": currency,
        "idem_key": idem_key
    }


def test_value_object_validations():
    # 1. Invalid Reference Format
    with pytest.raises(ValueError, match="must start with 'SET-'"):
        SettlementReference("BADREF-123")

    # 2. Invalid Batch Reference Format
    with pytest.raises(ValueError, match="must start with 'BATCH-'"):
        SettlementBatchReference("BADBATCH-123")

    # 3. Invalid Line Reference
    with pytest.raises(ValueError, match="must start with 'LINE-'"):
        SettlementLineReference("BADLINE-123")

    # 4. Invalid Merchant Reference
    with pytest.raises(ValueError, match="must start with 'MERCH-'"):
        MerchantReference("BADMERCH-123")

    # 5. Invalid Period Dates
    with pytest.raises(ValueError, match="Start date cannot be after end date"):
        SettlementPeriod(
            datetime(2026, 9, 10),
            datetime(2026, 9, 5)
        )

    # 6. Reject floats for SettlementAmount
    with pytest.raises(TypeError, match="Floating point numbers are not allowed"):
        SettlementAmount(150.75)


def test_valid_settlement_creation(base_setup):
    settlement_id = SettlementId(str(uuid4()))
    res = Settlement.create(
        id=settlement_id,
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    )
    assert res.is_success
    settlement = res.value
    assert settlement.status == SettlementStatus.DRAFT
    assert len(settlement.entries) == 0
    assert len(settlement.adjustments) == 0
    assert len(settlement.domain_events) == 1
    assert isinstance(settlement.domain_events[0], SettlementCreated)


def test_adding_valid_entries(base_setup):
    settlement = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    # Create entries
    entry1 = SettlementEntry(
        id=SettlementLineReference("LINE-ENT1"),
        source_transaction_reference="TX-101",
        source_context_type="INVOICE",
        gross_amount=SettlementAmount("1000.00"),
        fees_amount=SettlementAmount("20.00"),
        adjustments_amount=SettlementAmount("10.00"),
        net_amount=SettlementAmount("970.00"),
        currency=base_setup["currency"]
    )
    
    add_res = settlement.add_entry(entry1)
    assert add_res.is_success
    assert len(settlement.entries) == 1
    assert settlement.gross_amount.amount == Decimal("1000.00")
    assert settlement.fees_amount.amount == Decimal("20.00")
    assert settlement.adjustments_amount.amount == Decimal("10.00")
    assert settlement.net_amount.amount == Decimal("970.00")
    assert len(settlement.domain_events) == 2  # Created + EntryAdded
    assert isinstance(settlement.domain_events[1], SettlementEntryAdded)


def test_duplicate_entry_protection(base_setup):
    settlement = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    entry = SettlementEntry(
        id=SettlementLineReference("LINE-ENT1"),
        source_transaction_reference="TX-101",
        source_context_type="INVOICE",
        gross_amount=SettlementAmount("100.00"),
        fees_amount=SettlementAmount("2.00"),
        adjustments_amount=SettlementAmount("0.00"),
        net_amount=SettlementAmount("98.00"),
        currency=base_setup["currency"]
    )

    settlement.add_entry(entry)
    
    # Try adding again (same source transaction reference)
    res_dup = settlement.add_entry(entry)
    assert res_dup.is_failure
    assert "Cannot add" in res_dup.error.message


def test_entry_currency_mismatch(base_setup):
    settlement = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    different_currency = SettlementCurrency("EUR")
    entry = SettlementEntry(
        id=SettlementLineReference("LINE-ENT1"),
        source_transaction_reference="TX-101",
        source_context_type="INVOICE",
        gross_amount=SettlementAmount("100.00"),
        fees_amount=SettlementAmount("2.00"),
        adjustments_amount=SettlementAmount("0.00"),
        net_amount=SettlementAmount("98.00"),
        currency=different_currency
    )

    res = settlement.add_entry(entry)
    assert res.is_failure


def test_entry_internal_math_validation(base_setup):
    # net_amount must equal gross_amount - fees_amount - adjustments_amount
    with pytest.raises(ValueError, match="Entry amount mismatch"):
        SettlementEntry(
            id=SettlementLineReference("LINE-ENT1"),
            source_transaction_reference="TX-101",
            source_context_type="INVOICE",
            gross_amount=SettlementAmount("100.00"),
            fees_amount=SettlementAmount("2.00"),
            adjustments_amount=SettlementAmount("0.00"),
            # Expected is 98.00, we pass 95.00
            net_amount=SettlementAmount("95.00"),
            currency=base_setup["currency"]
        )


def test_applying_adjustments(base_setup):
    settlement = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    adj = SettlementAdjustment(
        id="adj-fee-01",
        type=AdjustmentType.PROCESSING_FEE,
        description="Standard monthly platform charge",
        amount=SettlementAmount("50.00"),
        currency=base_setup["currency"]
    )

    res = settlement.apply_adjustment(adj)
    assert res.is_success
    assert len(settlement.adjustments) == 1
    assert settlement.adjustments_amount.amount == Decimal("50.00")
    # Net amount goes negative since gross is 0 (0 - 0 - 50 = -50)
    assert settlement.net_amount.amount == Decimal("-50.00")
    assert isinstance(settlement.domain_events[1], SettlementAdjustmentApplied)


def test_successful_lifecycle_transitions(base_setup):
    settlement = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    # Add an entry to make it balance nicely
    entry = SettlementEntry(
        id=SettlementLineReference("LINE-ENT1"),
        source_transaction_reference="TX-101",
        source_context_type="INVOICE",
        gross_amount=SettlementAmount("1000.00"),
        fees_amount=SettlementAmount("20.00"),
        adjustments_amount=SettlementAmount("0.00"),
        net_amount=SettlementAmount("980.00"),
        currency=base_setup["currency"]
    )
    settlement.add_entry(entry)

    # 1. DRAFT -> OPEN
    op_res = settlement.open()
    assert op_res.is_success
    assert settlement.status == SettlementStatus.OPEN
    assert isinstance(settlement.domain_events[-1], SettlementOpened)

    # 2. OPEN -> RECONCILING
    rec_res = settlement.start_reconciliation()
    assert rec_res.is_success
    assert settlement.status == SettlementStatus.RECONCILING
    assert isinstance(settlement.domain_events[-1], SettlementReconciliationStarted)

    # 3. RECONCILING -> RECONCILED (All matched)
    result = ReconciliationResult(
        status=ReconciliationStatus.MATCHED,
        discrepancies=[],
        verified_at=Clock.now(),
        description="All entries reconciled perfectly."
    )
    match_res = settlement.reconcile(result)
    assert match_res.is_success
    assert settlement.status == SettlementStatus.RECONCILED
    assert settlement.reconciliation_state == ReconciliationStatus.MATCHED
    assert settlement.entries[0].reconciliation_state == ReconciliationStatus.MATCHED
    assert isinstance(settlement.domain_events[-1], SettlementReconciled)

    # 4. RECONCILED -> COMPLETED
    comp_res = settlement.complete()
    assert comp_res.is_success
    assert settlement.status == SettlementStatus.COMPLETED
    assert isinstance(settlement.domain_events[-1], SettlementCompleted)


def test_discrepancy_reconciliation_and_resolution(base_setup):
    settlement = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    entry = SettlementEntry(
        id=SettlementLineReference("LINE-ENT1"),
        source_transaction_reference="TX-101",
        source_context_type="INVOICE",
        gross_amount=SettlementAmount("100.00"),
        fees_amount=SettlementAmount("5.00"),
        adjustments_amount=SettlementAmount("0.00"),
        net_amount=SettlementAmount("95.00"),
        currency=base_setup["currency"]
    )
    settlement.add_entry(entry)

    settlement.open()
    settlement.start_reconciliation()

    # Mismatch discrepancy: entry TX-101 expected 95 but recorded 90
    discrepancy = ReconciliationDiscrepancy(
        entry_reference=SettlementLineReference("LINE-ENT1"),
        discrepancy_type="AMOUNT_MISMATCH",
        expected_amount=SettlementAmount("95.00"),
        actual_amount=SettlementAmount("90.00"),
        description="Entry recorded amount has 5.00 shortfall"
    )

    result = ReconciliationResult(
        status=ReconciliationStatus.DISCREPANCY,
        discrepancies=[discrepancy],
        verified_at=Clock.now(),
        description="Reconciliation found 1 mismatch."
    )

    # Reconciling -> DISCREPANCY
    reconcile_res = settlement.reconcile(result)
    assert reconcile_res.is_success
    assert settlement.status == SettlementStatus.DISCREPANCY
    assert settlement.reconciliation_state == ReconciliationStatus.DISCREPANCY
    assert isinstance(settlement.domain_events[-1], SettlementDiscrepancyDetected)

    # Attempt complete from DISCREPANCY (invalid)
    assert settlement.complete().is_failure

    # Resolve discrepancy
    resolve_res = settlement.resolve_discrepancy(SettlementLineReference("LINE-ENT1"))
    assert resolve_res.is_success
    assert settlement.status == SettlementStatus.RECONCILED
    assert settlement.reconciliation_state == ReconciliationStatus.MATCHED
    assert settlement.entries[0].reconciliation_state == ReconciliationStatus.MATCHED
    
    # Complete now works
    assert settlement.complete().is_success


def test_invalid_state_transitions(base_setup):
    settlement = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    # DRAFT -> RECONCILING (invalid)
    assert settlement.start_reconciliation().is_failure

    # DRAFT -> COMPLETED (invalid)
    assert settlement.complete().is_failure

    settlement.open()
    
    # OPEN -> COMPLETED (invalid)
    assert settlement.complete().is_failure


def test_cancellation_semantics(base_setup):
    settlement = Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=base_setup["org_id"],
        reference=base_setup["ref"],
        merchant_reference=base_setup["merchant_ref"],
        period=base_setup["period"],
        currency=base_setup["currency"],
        idempotency_key=base_setup["idem_key"]
    ).value

    # DRAFT -> CANCELLED (valid)
    assert settlement.cancel().is_success
    assert settlement.status == SettlementStatus.CANCELLED
    assert isinstance(settlement.domain_events[-1], SettlementCancelled)

    # Cancel cancelled (invalid)
    assert settlement.cancel().is_failure
