import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import Money, Currency, CurrencyType
from nomadx.src.domain.receivable.value_objects import ReceivableId, CustomerId, InvoiceId, AgingBucket
from nomadx.src.domain.receivable.receivable import Receivable, ReceivablePaymentStatus, ReceivableCollectionStatus
from nomadx.src.domain.receivable.events import ReceivableCreated, ReceivableBecameOverdue, ReceivableCollectionAttentionRequired
from nomadx.src.domain.receivable.policies import CollectionPolicy


class ControllableClock(Clock):
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


def test_aging_buckets(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"],  # Aug 1, 2026
        occurred_on=datetime(2026, 7, 20)
    ).value

    # Clock before due date
    clock = ControllableClock(datetime(2026, 7, 31))
    assert receivable.calculate_aging(clock) == AgingBucket.CURRENT

    # Clock on due date
    clock.set_time(datetime(2026, 8, 1))
    assert receivable.calculate_aging(clock) == AgingBucket.CURRENT

    # 1 day overdue
    clock.set_time(datetime(2026, 8, 2))
    assert receivable.calculate_aging(clock) == AgingBucket.DAYS_1_30

    # 30 days overdue
    clock.set_time(datetime(2026, 8, 31))
    assert receivable.calculate_aging(clock) == AgingBucket.DAYS_1_30

    # 31 days overdue
    clock.set_time(datetime(2026, 9, 1))
    assert receivable.calculate_aging(clock) == AgingBucket.DAYS_31_60

    # 61 days overdue
    clock.set_time(datetime(2026, 10, 1))
    assert receivable.calculate_aging(clock) == AgingBucket.DAYS_61_90

    # 91 days overdue
    clock.set_time(datetime(2026, 11, 1))
    assert receivable.calculate_aging(clock) == AgingBucket.DAYS_90_PLUS


def test_overdue_detection(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"],
        occurred_on=datetime(2026, 7, 20)
    ).value

    receivable.clear_domain_events()

    # check_overdue before due date has no effect
    receivable.check_overdue(datetime(2026, 7, 25))
    assert receivable.collection_status == ReceivableCollectionStatus.CURRENT
    assert len(receivable.domain_events) == 0

    # check_overdue after due date transitions to OVERDUE and emits became overdue event
    receivable.check_overdue(datetime(2026, 8, 2))
    assert receivable.collection_status == ReceivableCollectionStatus.OVERDUE
    assert len(receivable.domain_events) == 1
    assert isinstance(receivable.domain_events[0], ReceivableBecameOverdue)

    # running check_overdue again does not duplicate transition or event
    receivable.clear_domain_events()
    receivable.check_overdue(datetime(2026, 8, 3))
    assert receivable.collection_status == ReceivableCollectionStatus.OVERDUE
    assert len(receivable.domain_events) == 0


def test_fully_paid_receivable_does_not_become_overdue(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"],
        occurred_on=datetime(2026, 7, 20)
    ).value

    # Fully pay
    receivable.apply_payment_allocation(test_setup["original_amount"])
    assert receivable.collection_status == ReceivableCollectionStatus.RESOLVED

    receivable.clear_domain_events()

    # check_overdue does not mutate a resolved receivable
    receivable.check_overdue(datetime(2026, 8, 5))
    assert receivable.collection_status == ReceivableCollectionStatus.RESOLVED
    assert len(receivable.domain_events) == 0


def test_collection_attention_policy_transition(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"], # Aug 1, 2026
        occurred_on=datetime(2026, 7, 20)
    ).value

    policy = CollectionPolicy(overdue_attention_days=31)
    
    # 30 days overdue (Aug 31) -> stays OVERDUE (if checked)
    receivable.check_overdue(datetime(2026, 8, 31))
    days_overdue_30 = receivable.calculate_days_overdue(datetime(2026, 8, 31))
    assert days_overdue_30 == 30
    assert policy.requires_attention(days_overdue_30) is False

    # 31 days overdue (Sep 1) -> COLLECTION_ATTENTION & emits event
    receivable.clear_domain_events()
    days_overdue_31 = receivable.calculate_days_overdue(datetime(2026, 9, 1))
    assert days_overdue_31 == 31
    assert policy.requires_attention(days_overdue_31) is True

    receivable.trigger_collection_attention(datetime(2026, 9, 1))
    assert receivable.collection_status == ReceivableCollectionStatus.COLLECTION_ATTENTION
    assert len(receivable.domain_events) == 1
    assert isinstance(receivable.domain_events[0], ReceivableCollectionAttentionRequired)

    # Calling it again does not duplicate event or transition
    receivable.clear_domain_events()
    receivable.trigger_collection_attention(datetime(2026, 9, 2))
    assert receivable.collection_status == ReceivableCollectionStatus.COLLECTION_ATTENTION
    assert len(receivable.domain_events) == 0


def test_partial_payment_while_overdue_or_collection_attention(test_setup):
    rec_id = ReceivableId(str(uuid4()))
    receivable = Receivable.create(
        id=rec_id,
        organization_id=test_setup["org_id"],
        customer_id=test_setup["cust_id"],
        invoice_id=test_setup["inv_id"],
        original_amount=test_setup["original_amount"],
        due_date=test_setup["due_date"],
        occurred_on=datetime(2026, 7, 20)
    ).value

    # Overdue transition
    receivable.check_overdue(datetime(2026, 8, 5))
    assert receivable.collection_status == ReceivableCollectionStatus.OVERDUE

    # Partial payment after due date does not reset collection status to CURRENT
    alloc = Money(Decimal("3000.00"), test_setup["currency"])
    receivable.apply_payment_allocation(alloc, occurred_on=datetime(2026, 8, 6))
    assert receivable.payment_status == ReceivablePaymentStatus.PARTIALLY_PAID
    assert receivable.collection_status == ReceivableCollectionStatus.OVERDUE

    # Transition to Collection Attention
    receivable.trigger_collection_attention(datetime(2026, 9, 10))
    assert receivable.collection_status == ReceivableCollectionStatus.COLLECTION_ATTENTION

    # Another partial payment does not reset Collection Attention status
    receivable.apply_payment_allocation(alloc, occurred_on=datetime(2026, 9, 11))
    assert receivable.collection_status == ReceivableCollectionStatus.COLLECTION_ATTENTION

    # Full payment resolves it
    remaining_alloc = Money(Decimal("4000.00"), test_setup["currency"])
    receivable.apply_payment_allocation(remaining_alloc, occurred_on=datetime(2026, 9, 12))
    assert receivable.payment_status == ReceivablePaymentStatus.PAID
    assert receivable.collection_status == ReceivableCollectionStatus.RESOLVED
