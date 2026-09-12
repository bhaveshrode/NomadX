import pytest
import json
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import OutboxEventDbModel, ReceivableDbModel
from nomadx.src.infrastructure.database.repositories import PostgresReceivableRepository
from nomadx.src.infrastructure.workers.ar_event_processor import AREventProcessor
from nomadx.src.domain.receivable.value_objects import ReceivableId
from nomadx.src.domain.receivable.receivable import Receivable
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import Money, Currency, CurrencyType
from nomadx.src.domain.invoice import CustomerId, InvoiceId


class ControllableClock(Clock):
    _time = datetime(2026, 8, 2, tzinfo=timezone.utc)

    @classmethod
    def now(cls) -> datetime:
        return cls._time

    @classmethod
    def set_time(cls, new_time: datetime):
        cls._time = new_time


@pytest.fixture(autouse=True)
def patch_clock(monkeypatch):
    monkeypatch.setattr(Clock, "now", ControllableClock.now)


@pytest.mark.asyncio
async def test_payment_received_allocation_and_automatic_settlement(db_session: AsyncSession):
    org_id = str(uuid4())
    cust_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    receivable_repo = PostgresReceivableRepository(db_session)

    # 1. Create Receivable for 100 USDC
    ControllableClock.set_time(datetime(2026, 7, 20))
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(cust_id),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("100.00"), currency),
        due_date=datetime(2026, 8, 1),
        occurred_on=datetime(2026, 7, 20)
    ).value
    await receivable_repo.save(receivable)
    await db_session.commit()

    # 2. Simulate PaymentReceived outbox event of 60 USDC
    event_id_1 = str(uuid4())
    outbox_model = OutboxEventDbModel(
        id=event_id_1,
        event_type="PaymentReceived",
        payload=json.dumps({
            "payment_id": "pay-1",
            "invoice_id": inv_id,
            "allocated_amount": "60.00",
            "currency_code": "USDC",
            "organization_id": org_id
        }),
        processed=False,
        created_at=Clock.now()
    )
    db_session.add(outbox_model)
    await db_session.commit()

    # 3. Process payment event
    processor = AREventProcessor()
    processed_count = await processor.process_payment_events()
    assert processed_count == 1

    # Verify partial payment state
    async with session_manager.session() as check_session:
        check_repo = PostgresReceivableRepository(check_session)
        rec = await check_repo.get_by_id(ReceivableId(rec_id))
        assert rec.status.value == "PARTIALLY_PAID"
        assert rec.outstanding_amount.amount == Decimal("40.00")
        assert len(rec.allocations) == 1

    # 4. Processing same event again must be idempotent (0 processed)
    processed_count = await processor.process_payment_events()
    assert processed_count == 0

    # 5. Simulate second payment event for 40 USDC to trigger automatic settlement
    event_id_2 = str(uuid4())
    outbox_model_2 = OutboxEventDbModel(
        id=event_id_2,
        event_type="PaymentReceived",
        payload=json.dumps({
            "payment_id": "pay-2",
            "invoice_id": inv_id,
            "allocated_amount": "40.00",
            "currency_code": "USDC",
            "organization_id": org_id
        }),
        processed=False,
        created_at=Clock.now()
    )
    async with session_manager.session() as write_session:
        write_session.add(outbox_model_2)
        await write_session.commit()

    processed_count = await processor.process_payment_events()
    assert processed_count == 1

    # Verify settled state
    async with session_manager.session() as check_session:
        check_repo = PostgresReceivableRepository(check_session)
        rec = await check_repo.get_by_id(ReceivableId(rec_id))
        assert rec.status.value == "SETTLED"
        assert rec.outstanding_amount.amount == Decimal("0.00")
        assert len(rec.allocations) == 2


@pytest.mark.asyncio
async def test_payment_refund_and_reversal(db_session: AsyncSession):
    org_id = str(uuid4())
    cust_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    receivable_repo = PostgresReceivableRepository(db_session)

    # 1. Create settled receivable (allocated = 100, outstanding = 0)
    ControllableClock.set_time(datetime(2026, 7, 20))
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(cust_id),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("100.00"), currency),
        due_date=datetime(2026, 8, 1),
        occurred_on=datetime(2026, 7, 20)
    ).value
    # Apply initial allocation
    receivable.allocate_payment(payment_id="pay-1", amount=Money(Decimal("100.00"), currency), allocation_reference="alloc-1")
    await receivable_repo.save(receivable)
    await db_session.commit()

    # 2. Simulate PaymentRefunded outbox event of 40 USDC
    event_id = str(uuid4())
    outbox_model = OutboxEventDbModel(
        id=event_id,
        event_type="PaymentRefunded",
        payload=json.dumps({
            "payment_id": "pay-1",
            "invoice_id": inv_id,
            "allocated_amount": "40.00",
            "currency_code": "USDC",
            "organization_id": org_id
        }),
        processed=False,
        created_at=Clock.now()
    )
    db_session.add(outbox_model)
    await db_session.commit()

    # 3. Process refund event
    processor = AREventProcessor()
    processed_count = await processor.process_payment_events()
    assert processed_count == 1

    # Verify status changed back to PARTIALLY_PAID and outstanding balance increased
    async with session_manager.session() as check_session:
        check_repo = PostgresReceivableRepository(check_session)
        rec = await check_repo.get_by_id(ReceivableId(rec_id))
        assert rec.status.value == "PARTIALLY_PAID"
        assert rec.outstanding_amount.amount == Decimal("40.00")
        assert rec.allocated_amount.amount == Decimal("60.00")
        assert len(rec.allocations) == 2 # 1 normal allocation + 1 negative refund allocation
