import pytest
import json
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import OutboxEventDbModel, ReceivableDbModel
from nomadx.src.infrastructure.database.repositories import PostgresReceivableRepository
from nomadx.src.infrastructure.workers.receivable_aging_worker import ReceivableAgingWorker
from nomadx.src.domain.receivable.value_objects import ReceivableId
from nomadx.src.domain.receivable.receivable import Receivable, ReceivableCollectionStatus
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
async def test_aging_bucket_transitions_and_idempotency(db_session: AsyncSession):
    # Setup test entities
    org_id = str(uuid4())
    cust_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    receivable_repo = PostgresReceivableRepository(db_session)

    # 1. Create a Receivable in CURRENT status
    ControllableClock.set_time(datetime(2026, 7, 20))
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(cust_id),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("10000.00"), currency),
        due_date=datetime(2026, 8, 1),
        occurred_on=datetime(2026, 7, 20)
    ).value
    await receivable_repo.save(receivable)
    await db_session.commit()

    # 2. Advance clock past due date (August 5 -> 4 days overdue, bucket should become DAYS_1_30)
    ControllableClock.set_time(datetime(2026, 8, 5))
    worker = ReceivableAgingWorker()
    count = await worker.process_aging()
    assert count == 1

    # Verify bucket and outbox events
    async with session_manager.session() as check_session:
        db_stmt = select(ReceivableDbModel).where(ReceivableDbModel.id == rec_id)
        db_rec = (await check_session.execute(db_stmt)).scalar_one()
        assert db_rec.aging_bucket == "DAYS_1_30"

        stmt = select(OutboxEventDbModel).where(
            OutboxEventDbModel.event_type == "ReceivableAgingThresholdReached",
            OutboxEventDbModel.processed == False
        )
        events = (await check_session.execute(stmt)).scalars().all()
        assert len(events) == 1
        payload = json.loads(events[0].payload)
        assert payload["aging_bucket"] == "DAYS_1_30"

        # Mark processed
        events[0].processed = True
        await check_session.commit()

    # 3. Running worker again should be idempotent (no duplicate events)
    count = await worker.process_aging()
    assert count == 0 # no changes saved during idempotent run
    async with session_manager.session() as check_session:
        stmt = select(OutboxEventDbModel).where(
            OutboxEventDbModel.event_type == "ReceivableAgingThresholdReached",
            OutboxEventDbModel.processed == False
        )
        events = (await check_session.execute(stmt)).scalars().all()
        assert len(events) == 0 # no new events emitted since bucket remained same

    # 4. Shift clock further (September 5 -> 35 days overdue, bucket becomes DAYS_31_60)
    ControllableClock.set_time(datetime(2026, 9, 5))
    count = await worker.process_aging()
    assert count == 1

    async with session_manager.session() as check_session:
        db_stmt = select(ReceivableDbModel).where(ReceivableDbModel.id == rec_id)
        db_rec = (await check_session.execute(db_stmt)).scalar_one()
        assert db_rec.aging_bucket == "DAYS_31_60"

        stmt = select(OutboxEventDbModel).where(
            OutboxEventDbModel.event_type == "ReceivableAgingThresholdReached",
            OutboxEventDbModel.processed == False
        )
        events = (await check_session.execute(stmt)).scalars().all()
        assert len(events) == 1
        payload = json.loads(events[0].payload)
        assert payload["aging_bucket"] == "DAYS_31_60"
