import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.apps.api.main import app
from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import OutboxEventDbModel, ReceivableDbModel
from nomadx.src.infrastructure.database.repositories import PostgresReceivableRepository
from nomadx.src.infrastructure.workers.receivable_aging_worker import ReceivableAgingWorker
from nomadx.src.domain.receivable.value_objects import ReceivableId
from nomadx.src.domain.receivable.receivable import Receivable, ReceivableCollectionStatus, ReceivablePaymentStatus
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
async def test_aging_worker_e2e_and_idempotency(db_session: AsyncSession):
    # Setup test variables
    org_id = str(uuid4())
    cust_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    receivable_repo = PostgresReceivableRepository(db_session)

    # 1. Create a Receivable due on Aug 1, 2026, occurred on July 20, 2026
    # Let's set clock to July 20 to create it in CURRENT status
    ControllableClock.set_time(datetime(2026, 7, 20))
    
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(cust_id),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("10000.00"), currency),
        due_date=datetime(2026, 8, 1), # Aug 1
        occurred_on=datetime(2026, 7, 20)
    ).value
    await receivable_repo.save(receivable)
    await db_session.commit()

    # Verify initially CURRENT
    async with session_manager.session() as check_session:
        check_repo = PostgresReceivableRepository(check_session)
        rec = await check_repo.get_by_id(ReceivableId(rec_id))
        assert rec.collection_status == ReceivableCollectionStatus.CURRENT

    # 2. Set Clock to Aug 5, 2026 (past due date)
    ControllableClock.set_time(datetime(2026, 8, 5))

    worker = ReceivableAgingWorker()
    processed_count = await worker.process_aging()
    assert processed_count == 1

    # Verify status changed to OVERDUE and outbox event emitted
    async with session_manager.session() as check_session:
        check_repo = PostgresReceivableRepository(check_session)
        rec = await check_repo.get_by_id(ReceivableId(rec_id))
        assert rec.collection_status == ReceivableCollectionStatus.OVERDUE

        # Outbox event assertion
        stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "ReceivableBecameOverdue")
        res = await check_session.execute(stmt)
        events = res.scalars().all()
        assert len(events) == 1

    # 3. Run worker again. It should be idempotent (0 processed, no duplicate events)
    processed_count_dup = await worker.process_aging()
    assert processed_count_dup == 0

    async with session_manager.session() as check_session:
        stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "ReceivableBecameOverdue")
        res = await check_session.execute(stmt)
        events = res.scalars().all()
        assert len(events) == 1 # still only 1 event

    # 4. Set Clock to Sep 5, 2026 (35 days overdue, meets Collection Attention Policy threshold)
    ControllableClock.set_time(datetime(2026, 9, 5))
    processed_attention = await worker.process_aging()
    assert processed_attention == 1

    # Verify status changed to COLLECTION_ATTENTION and outbox event emitted
    async with session_manager.session() as check_session:
        check_repo = PostgresReceivableRepository(check_session)
        rec = await check_repo.get_by_id(ReceivableId(rec_id))
        assert rec.collection_status == ReceivableCollectionStatus.COLLECTION_ATTENTION

        # Outbox event assertion
        stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "ReceivableCollectionAttentionRequired")
        res = await check_session.execute(stmt)
        events = res.scalars().all()
        assert len(events) == 1


@pytest.mark.asyncio
async def test_aging_api_reporting_and_isolation():
    ControllableClock.set_time(datetime(2026, 9, 5))

    email_a = f"aging_merchant_a_{uuid4()}@nomadx.com"
    email_b = f"aging_merchant_b_{uuid4()}@nomadx.com"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register Merchant A
        reg_a = await ac.post("/auth/register", json={
            "email": email_a,
            "password": "strongpassword123",
            "name": "Merchant A",
            "organization_name": "Org A Corp",
            "organization_slug": f"aging-org-a-{uuid4()}",
            "default_currency": "USD"
        })
        assert reg_a.status_code == 201
        
        login_a = await ac.post("/auth/login", json={
            "email": email_a,
            "password": "strongpassword123"
        })
        token_a = login_a.json()["token"]

        ctx_res_a = await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_a}"})
        org_a_id = ctx_res_a.json()["organization_id"]

        # Register Merchant B
        reg_b = await ac.post("/auth/register", json={
            "email": email_b,
            "password": "strongpassword123",
            "name": "Merchant B",
            "organization_name": "Org B Corp",
            "organization_slug": f"aging-org-b-{uuid4()}",
            "default_currency": "USD"
        })
        assert reg_b.status_code == 201

        login_b = await ac.post("/auth/login", json={
            "email": email_b,
            "password": "strongpassword123"
        })
        token_b = login_b.json()["token"]

        # Setup A's receivable: $10,000, due Aug 1 (35 days overdue -> 31_60 bucket)
        async with session_manager.session() as session:
            repo = PostgresReceivableRepository(session)
            currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
            rec_a = Receivable.create(
                id=ReceivableId(str(uuid4())),
                organization_id=OrganizationId(org_a_id),
                customer_id=CustomerId(str(uuid4())),
                invoice_id=InvoiceId(str(uuid4())),
                original_amount=Money(Decimal("10000.00"), currency),
                due_date=datetime(2026, 8, 1),
                occurred_on=datetime(2026, 7, 20)
            ).value
            await repo.save(rec_a)
            await session.commit()

        # Let the worker evaluate A
        worker = ReceivableAgingWorker()
        await worker.process_aging()

        # 1. API Call: GET A's aging summary. Ensure B's data is isolated.
        res_summary = await ac.get("/api/receivables/aging", headers={"Authorization": f"Bearer {token_a}"})
        assert res_summary.status_code == 200
        summary = res_summary.json()
        assert summary["days_31_60"] == "10000.00"
        assert summary["total_outstanding"] == "10000.00"

        # 2. API Call: GET B's aging summary (should be zero/empty)
        res_summary_b = await ac.get("/api/receivables/aging", headers={"Authorization": f"Bearer {token_b}"})
        assert res_summary_b.status_code == 200
        summary_b = res_summary_b.json()
        assert summary_b["total_outstanding"] == "0.00"

        # 3. API Call: GET A's receivables list with filters
        res_list = await ac.get("/api/receivables?aging_bucket=DAYS_31_60", headers={"Authorization": f"Bearer {token_a}"})
        assert res_list.status_code == 200
        items = res_list.json()
        assert len(items) == 1
        assert items[0]["original_amount"] == "10000.00"
        assert items[0]["collection_status"] == "COLLECTION_ATTENTION"

        # 4. API Call: GET A's receivable by ID
        target_id = items[0]["id"]
        res_detail = await ac.get(f"/api/receivables/{target_id}", headers={"Authorization": f"Bearer {token_a}"})
        assert res_detail.status_code == 200
        assert res_detail.json()["id"] == target_id

        # 5. Tenant safety: GET B tries to fetch A's receivable by ID -> returns 404
        res_forbidden = await ac.get(f"/api/receivables/{target_id}", headers={"Authorization": f"Bearer {token_b}"})
        assert res_forbidden.status_code == 404


@pytest.mark.asyncio
async def test_aging_transactional_rollback_on_failure(db_session: AsyncSession):
    # Setup test variables
    org_id = str(uuid4())
    cust_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    receivable_repo = PostgresReceivableRepository(db_session)

    ControllableClock.set_time(datetime(2026, 7, 20))
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(cust_id),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("5000.00"), currency),
        due_date=datetime(2026, 8, 1),
        occurred_on=datetime(2026, 7, 20)
    ).value
    await receivable_repo.save(receivable)
    await db_session.commit()

    # Move time forward
    ControllableClock.set_time(datetime(2026, 8, 5))

    # Break save or outbox emission by raising error on save inside a custom worker execution
    worker = ReceivableAgingWorker()
    
    # We monkeypatch PostgresReceivableRepository.save to raise an exception
    original_save = PostgresReceivableRepository.save
    
    async def failing_save(self, rec):
        raise RuntimeError("Database Connection Dropped")

    # Using python built-in mock/patch context or direct override
    try:
        PostgresReceivableRepository.save = failing_save
        with pytest.raises(RuntimeError, match="Database Connection"):
            await worker.process_aging()
    finally:
        # Restore save
        PostgresReceivableRepository.save = original_save

    # Verify both status update and outbox event are rolled back
    async with session_manager.session() as check_session:
        check_repo = PostgresReceivableRepository(check_session)
        rec = await check_repo.get_by_id(ReceivableId(rec_id))
        assert rec.collection_status == ReceivableCollectionStatus.CURRENT # rolled back!

        # Outbox event assertion (should be empty)
        stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "ReceivableBecameOverdue")
        res = await check_session.execute(stmt)
        events = res.scalars().all()
        assert len(events) == 0 # rolled back!
