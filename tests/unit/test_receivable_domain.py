import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import Money, Currency, CurrencyType
from nomadx.src.domain.receivable.value_objects import ReceivableId, CustomerId, InvoiceId, AgingBucket
from nomadx.src.domain.receivable.receivable import (
    Receivable,
    ReceivablePaymentStatus,
    ReceivableCollectionStatus
)
from nomadx.src.domain.receivable.events import (
    ReceivableCreated,
    ReceivablePartiallyPaid,
    ReceivablePaid,
    ReceivableBecameOverdue
)
from nomadx.src.domain.receivable.specifications import (
    ReceivableCanAcceptAllocation,
    ReceivableIsOutstanding,
    ReceivableIsOverdue,
    ReceivableIsFullyPaid
)


class ControllableClock(Clock):
    """A clock utility for testing that returns a controllable timestamp."""
    def __init__(self, time_val: datetime):
        self._time = time_val

    def now(self) -> datetime:
        return self._time

    def set_time(self, new_time: datetime):
        self._time = new_time


@pytest.fixture
def test_setup():
    org_id = OrganizationId(str(uuid4()))
    cust_id = CustomerId(str(uuid4()))
    inv_id = InvoiceId(str(uuid4()))
    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    original_amount = Money(Decimal("10000.00"), currency)
    due_date = datetime(2026, 8, 1)
    
    return {
        "org_id": org_id,
        "cust_id": cust_id,
        "inv_id": inv_id,
        "currency": currency,
        "original_amount": original_amount,
        "due_date": due_date
    }


def test_receivable_creation(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    created_at = datetime(2026, 7, 15)
    
    res = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"],
        occurred_on=created_at
    )
    
    assert res.is_success
    receivable = res.value
    assert receivable.id == rec_id
    assert receivable.organization_id == test_setup["org_id"]
    assert receivable.customer_id == test_setup["cust_id"]
    assert receivable.invoice_id == test_setup["inv_id"]
    assert receivable.original_amount == test_setup["original_amount"]
    assert receivable.paid_amount.amount == Decimal("0.00")
    assert receivable.outstanding_amount == test_setup["original_amount"]
    assert receivable.currency == test_setup["currency"]
    assert receivable.due_date == test_setup["due_date"]
    assert receivable.payment_status == ReceivablePaymentStatus.UNPAID
    assert receivable.collection_status == ReceivableCollectionStatus.CURRENT
    
    # Check Domain Event
    assert len(receivable.domain_events) == 1
    event = receivable.domain_events[0]
    assert isinstance(event, ReceivableCreated)
    assert event.receivable_id == rec_id
    assert event.organization_id == test_setup["org_id"]
    assert event.invoice_id == test_setup["inv_id"]
    assert event.customer_id == test_setup["cust_id"]
    assert event.original_amount == test_setup["original_amount"]
    assert event.due_date == test_setup["due_date"]
    assert event.occurred_on == created_at


def test_receivable_creation_validations(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    
    # Zero amount original rejected
    zero_money = Money(Decimal("0.00"), test_setup["currency"])
    res_zero = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=zero_money,
        due_date=test_setup["due_date"]
    )
    assert res_zero.is_failure
    assert "greater than zero" in res_zero.error.message

    # Negative amount original rejected
    neg_money = Money(Decimal("-500.00"), test_setup["currency"])
    res_neg = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=neg_money,
        due_date=test_setup["due_date"]
    )
    assert res_neg.is_failure
    assert "greater than zero" in res_neg.error.message

    # Missing fields validation
    res_missing = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=None,
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"]
    )
    assert res_missing.is_failure
    assert "must be provided" in res_missing.error.message


def test_receivable_exact_payment_allocation(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"],
        occurred_on=datetime(2026, 7, 15)
    ).value
    
    receivable.clear_domain_events()
    
    # Apply exact allocation ($10,000)
    alloc_money = Money(Decimal("10000.00"), test_setup["currency"])
    res_alloc = receivable.apply_payment_allocation(alloc_money, occurred_on=datetime(2026, 7, 20))
    
    assert res_alloc.is_success
    assert receivable.paid_amount.amount == Decimal("10000.00")
    assert receivable.outstanding_amount.amount == Decimal("0.00")
    assert receivable.payment_status == ReceivablePaymentStatus.PAID
    assert receivable.collection_status == ReceivableCollectionStatus.RESOLVED

    # Check event
    assert len(receivable.domain_events) == 1
    event = receivable.domain_events[0]
    assert isinstance(event, ReceivablePaid)
    assert event.receivable_id == rec_id
    assert event.organization_id == test_setup["org_id"]
    assert event.allocated_amount == alloc_money
    assert event.paid_amount == receivable.paid_amount
    assert event.occurred_on == datetime(2026, 7, 20)


def test_receivable_partial_and_multiple_allocations(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"],
        occurred_on=datetime(2026, 7, 15)
    ).value
    
    receivable.clear_domain_events()

    # 1. First allocation: $3,000
    alloc1 = Money(Decimal("3000.00"), test_setup["currency"])
    res1 = receivable.apply_payment_allocation(alloc1, occurred_on=datetime(2026, 7, 16))
    assert res1.is_success
    assert receivable.paid_amount.amount == Decimal("3000.00")
    assert receivable.outstanding_amount.amount == Decimal("7000.00")
    assert receivable.payment_status == ReceivablePaymentStatus.PARTIALLY_PAID
    assert receivable.collection_status == ReceivableCollectionStatus.CURRENT

    assert len(receivable.domain_events) == 1
    event1 = receivable.domain_events[0]
    assert isinstance(event1, ReceivablePartiallyPaid)
    assert event1.allocated_amount == alloc1
    assert event1.paid_amount.amount == Decimal("3000.00")
    assert event1.outstanding_amount.amount == Decimal("7000.00")

    # 2. Second allocation: $4,000
    receivable.clear_domain_events()
    alloc2 = Money(Decimal("4000.00"), test_setup["currency"])
    res2 = receivable.apply_payment_allocation(alloc2, occurred_on=datetime(2026, 7, 17))
    assert res2.is_success
    assert receivable.paid_amount.amount == Decimal("7000.00")
    assert receivable.outstanding_amount.amount == Decimal("3000.00")
    assert receivable.payment_status == ReceivablePaymentStatus.PARTIALLY_PAID
    assert receivable.collection_status == ReceivableCollectionStatus.CURRENT

    assert len(receivable.domain_events) == 1
    event2 = receivable.domain_events[0]
    assert isinstance(event2, ReceivablePartiallyPaid)
    assert event2.allocated_amount == alloc2

    # 3. Third allocation: $3,000 (completes payment)
    receivable.clear_domain_events()
    alloc3 = Money(Decimal("3000.00"), test_setup["currency"])
    res3 = receivable.apply_payment_allocation(alloc3, occurred_on=datetime(2026, 7, 18))
    assert res3.is_success
    assert receivable.paid_amount.amount == Decimal("10000.00")
    assert receivable.outstanding_amount.amount == Decimal("0.00")
    assert receivable.payment_status == ReceivablePaymentStatus.PAID
    assert receivable.collection_status == ReceivableCollectionStatus.RESOLVED

    assert len(receivable.domain_events) == 1
    event3 = receivable.domain_events[0]
    assert isinstance(event3, ReceivablePaid)


def test_receivable_over_allocation_rejected(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"]
    ).value

    # Try applying $12,000 to a $10,000 receivable
    alloc_too_high = Money(Decimal("12000.00"), test_setup["currency"])
    res = receivable.apply_payment_allocation(alloc_too_high)
    assert res.is_failure
    assert "exceeds outstanding balance" in res.error.message
    
    # Original state preserved
    assert receivable.paid_amount.amount == Decimal("0.00")
    assert receivable.outstanding_amount.amount == Decimal("10000.00")


def test_receivable_allocation_validations(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"]
    ).value

    # Mismatched Currency allocation rejected
    fiat_usd = Currency("USD", CurrencyType.FIAT)
    mismatched_money = Money(Decimal("1000.00"), fiat_usd)
    res_currency = receivable.apply_payment_allocation(mismatched_money)
    assert res_currency.is_failure
    assert "currency mismatch" in res_currency.error.message

    # Zero amount allocation rejected
    zero_money = Money(Decimal("0.00"), test_setup["currency"])
    res_zero = receivable.apply_payment_allocation(zero_money)
    assert res_zero.is_failure
    assert "greater than zero" in res_zero.error.message

    # Negative amount allocation rejected
    neg_money = Money(Decimal("-50.00"), test_setup["currency"])
    res_neg = receivable.apply_payment_allocation(neg_money)
    assert res_neg.is_failure
    assert "greater than zero" in res_neg.error.message


def test_receivable_aging_buckets(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"] # August 1, 2026
    ).value
    
    # Setup Controllable Clock
    clock = ControllableClock(datetime(2026, 7, 20)) # Before due date

    # 1. Before due date -> CURRENT
    assert receivable.calculate_days_overdue(clock.now()) == 0
    assert receivable.calculate_aging(clock) == AgingBucket.CURRENT

    # 2. On due date -> CURRENT
    clock.set_time(datetime(2026, 8, 1))
    assert receivable.calculate_days_overdue(clock.now()) == 0
    assert receivable.calculate_aging(clock) == AgingBucket.CURRENT

    # 3. 1 day overdue -> DAYS_1_30
    clock.set_time(datetime(2026, 8, 2))
    assert receivable.calculate_days_overdue(clock.now()) == 1
    assert receivable.calculate_aging(clock) == AgingBucket.DAYS_1_30

    # 4. 30 days overdue -> DAYS_1_30
    clock.set_time(datetime(2026, 8, 31))
    assert receivable.calculate_days_overdue(clock.now()) == 30
    assert receivable.calculate_aging(clock) == AgingBucket.DAYS_1_30

    # 5. 31 days overdue -> DAYS_31_60
    clock.set_time(datetime(2026, 9, 1))
    assert receivable.calculate_days_overdue(clock.now()) == 31
    assert receivable.calculate_aging(clock) == AgingBucket.DAYS_31_60

    # 6. 61 days overdue -> DAYS_61_90
    clock.set_time(datetime(2026, 10, 1))
    assert receivable.calculate_days_overdue(clock.now()) == 61
    assert receivable.calculate_aging(clock) == AgingBucket.DAYS_61_90

    # 7. 91 days overdue -> DAYS_90_PLUS
    clock.set_time(datetime(2026, 11, 1))
    assert receivable.calculate_days_overdue(clock.now()) == 92
    assert receivable.calculate_aging(clock) == AgingBucket.DAYS_90_PLUS


def test_collection_status_transitions_and_became_overdue_event(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"], # August 1, 2026
        occurred_on=datetime(2026, 7, 20)
    ).value

    assert receivable.collection_status == ReceivableCollectionStatus.CURRENT
    receivable.clear_domain_events()

    # 1. check_overdue before due date (no change)
    receivable.check_overdue(datetime(2026, 7, 25))
    assert receivable.collection_status == ReceivableCollectionStatus.CURRENT
    assert len(receivable.domain_events) == 0

    # 2. check_overdue after due date -> OVERDUE status & became overdue event
    receivable.check_overdue(datetime(2026, 8, 2))
    assert receivable.collection_status == ReceivableCollectionStatus.OVERDUE
    assert len(receivable.domain_events) == 1
    event = receivable.domain_events[0]
    assert isinstance(event, ReceivableBecameOverdue)
    assert event.receivable_id == rec_id
    assert event.organization_id == test_setup["org_id"]
    assert event.occurred_on == datetime(2026, 8, 2)

    # 3. Pay fully -> RESOLVED
    receivable.clear_domain_events()
    alloc = Money(Decimal("10000.00"), test_setup["currency"])
    receivable.apply_payment_allocation(alloc)
    assert receivable.collection_status == ReceivableCollectionStatus.RESOLVED


def test_receivable_specifications(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"]
    ).value

    # Initially: can accept allocation, outstanding, not overdue, not fully paid
    assert ReceivableCanAcceptAllocation().is_satisfied_by(receivable) is True
    assert ReceivableIsOutstanding().is_satisfied_by(receivable) is True
    assert ReceivableIsOverdue(datetime(2026, 7, 20)).is_satisfied_by(receivable) is False
    assert ReceivableIsFullyPaid().is_satisfied_by(receivable) is False

    # Overdue check: past due date
    assert ReceivableIsOverdue(datetime(2026, 8, 5)).is_satisfied_by(receivable) is True

    # Fully paid specifications check
    alloc = Money(Decimal("10000.00"), test_setup["currency"])
    receivable.apply_payment_allocation(alloc)
    assert ReceivableCanAcceptAllocation().is_satisfied_by(receivable) is False
    assert ReceivableIsOutstanding().is_satisfied_by(receivable) is False
    assert ReceivableIsOverdue(datetime(2026, 8, 5)).is_satisfied_by(receivable) is False
    assert ReceivableIsFullyPaid().is_satisfied_by(receivable) is True


def test_tenant_isolation_mismatch_creation(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    
    # Preserves Organization ID matching test_setup org_id
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"]
    ).value
    
    assert receivable.organization_id == test_setup["org_id"]


def test_receivable_advanced_value_objects_validation(test_setup):
    from nomadx.src.domain.receivable.value_objects import (
        ReceivableReference, CustomerReference, InvoiceReference, ReceivableIdempotencyKey
    )

    # 1. ReceivableReference validations
    ref = ReceivableReference("AR-12345")
    assert ref.value == "AR-12345"
    assert str(ref) == "AR-12345"

    with pytest.raises(ValueError, match="must start with 'AR-'"):
        ReceivableReference("INVALID-REF")

    with pytest.raises(ValueError, match="cannot be empty"):
        ReceivableReference("")

    # 2. CustomerReference validations
    cust_ref = CustomerReference("CUST-abc-123")
    assert cust_ref.value == "CUST-abc-123"

    with pytest.raises(ValueError, match="must start with 'CUST-'"):
        CustomerReference("CUST_INVALID")

    # 3. InvoiceReference validations
    inv_ref = InvoiceReference("INV-invoice-99")
    assert inv_ref.value == "INV-invoice-99"

    inv_ref_alt = InvoiceReference("INV_123/456-abc")
    assert inv_ref_alt.value == "INV_123/456-abc"

    with pytest.raises(ValueError, match="must start with 'INV-' or follow the default invoice number format"):
        InvoiceReference("INVALID_NUMBER_@@@")

    # 4. Idempotency Key
    idem = ReceivableIdempotencyKey("KEY-123")
    assert idem.value == "KEY-123"


def test_receivable_allocation_and_dunning_stage(test_setup):
    from nomadx.src.domain.receivable.value_objects import (
        ReceivableReference, CustomerReference, InvoiceReference, ReceivableIdempotencyKey
    )
    from nomadx.src.domain.receivable.dunning import DunningStage, DunningState
    from nomadx.src.domain.receivable.events import ReceivablePaymentAllocated, DunningStageChanged
    from nomadx.src.domain.receivable.specifications import CanAdvanceDunningStage

    rec_id = ReceivableId(str(uuid4()))
    ref = ReceivableReference("AR-DUNNING-TEST")
    cust_ref = CustomerReference("CUST-buyer")
    inv_ref = InvoiceReference("INV-invoice-d")
    idem = ReceivableIdempotencyKey("idem-test")

    res = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"],
        reference=ref,
        customer_reference=cust_ref,
        invoice_reference=inv_ref,
        idempotency_key=idem,
        occurred_on=datetime(2026, 7, 20)
    )
    receivable = res.value
    assert receivable.reference == ref
    assert receivable.customer_reference == cust_ref
    assert receivable.invoice_reference == inv_ref
    assert receivable.idempotency_key == idem

    # Assert initial dunning stage
    assert receivable.dunning_state.stage == DunningStage.NONE
    assert receivable.dunning_state.attempts == 0
    assert CanAdvanceDunningStage().is_satisfied_by(receivable) is True

    # 1. Advance Dunning Stage
    receivable.clear_domain_events()
    res_adv = receivable.advance_dunning_stage(DunningStage.FIRST_REMINDER, occurred_on=datetime(2026, 7, 22))
    assert res_adv.is_success
    assert receivable.dunning_state.stage == DunningStage.FIRST_REMINDER
    assert receivable.dunning_state.attempts == 1
    assert receivable.dunning_state.last_attempt_at == datetime(2026, 7, 22)

    assert len(receivable.domain_events) == 1
    event = receivable.domain_events[0]
    assert isinstance(event, DunningStageChanged)
    assert event.previous_stage == DunningStage.NONE
    assert event.new_stage == DunningStage.FIRST_REMINDER

    # 2. Allocate payment using allocate_payment
    receivable.clear_domain_events()
    alloc_money = Money(Decimal("4000.00"), test_setup["currency"])
    res_alloc = receivable.allocate_payment(
        payment_id="pay-abc-123",
        amount=alloc_money,
        allocation_reference="alloc-ref-777",
        occurred_on=datetime(2026, 7, 25)
    )
    assert res_alloc.is_success
    assert receivable.outstanding_amount.amount == Decimal("6000.00")
    assert len(receivable.allocations) == 1
    assert receivable.allocations[0].payment_id == "pay-abc-123"
    assert receivable.allocations[0].allocation_reference == "alloc-ref-777"

    # Allocations should trigger both PartiallyPaid and ReceivablePaymentAllocated
    assert len(receivable.domain_events) == 2
    assert any(isinstance(ev, ReceivablePaymentAllocated) for ev in receivable.domain_events)


def test_receivable_write_off(test_setup):
    from nomadx.src.domain.receivable.receivable import ReceivableStatus
    from nomadx.src.domain.receivable.events import ReceivableWrittenOff
    from nomadx.src.domain.receivable.specifications import CanWriteOff

    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"]
    ).value

    assert CanWriteOff().is_satisfied_by(receivable) is True

    # Attempt write-off
    receivable.clear_domain_events()
    res_wo = receivable.write_off(reason="Customer declared bankruptcy", occurred_on=datetime(2026, 8, 10))
    assert res_wo.is_success
    assert receivable.status == ReceivableStatus.WRITTEN_OFF
    assert CanWriteOff().is_satisfied_by(receivable) is False

    assert len(receivable.domain_events) == 1
    event = receivable.domain_events[0]
    assert isinstance(event, ReceivableWrittenOff)
    assert event.write_off_reason == "Customer declared bankruptcy"


def test_receivable_aging_threshold_events(test_setup):
    from nomadx.src.domain.receivable.events import ReceivableAgingThresholdReached
    
    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"] # Aug 1, 2026
    ).value

    clock = ControllableClock(datetime(2026, 7, 25))

    # 1. Aging initially CURRENT
    receivable.clear_domain_events()
    bucket = receivable.update_aging_and_detect_threshold(clock)
    assert bucket == AgingBucket.CURRENT
    assert len(receivable.domain_events) == 0

    # 2. Aging transitions to DAYS_1_30 -> threshold reached event emitted!
    clock.set_time(datetime(2026, 8, 5))
    bucket = receivable.update_aging_and_detect_threshold(clock)
    assert bucket == AgingBucket.DAYS_1_30
    assert len(receivable.domain_events) == 1
    assert isinstance(receivable.domain_events[0], ReceivableAgingThresholdReached)
    assert receivable.domain_events[0].aging_bucket == AgingBucket.DAYS_1_30

