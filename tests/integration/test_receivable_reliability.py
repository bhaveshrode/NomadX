import pytest
import json
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient, ASGITransport

from nomadx.apps.api.main import app
from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import (
    ReceivableDbModel,
    ReceivableCollectionActionDbModel,
    OutboxEventDbModel,
    InvoiceDbModel
)
from nomadx.src.infrastructure.database.repositories import (
    PostgresReceivableRepository,
    PostgresCollectionActionRepository
)
from nomadx.src.infrastructure.workers.dunning_worker import DunningWorker
from nomadx.src.application.receivable.dunning_policy import DunningPolicy
from nomadx.src.infrastructure.workers.collection_action_worker import CollectionActionWorker
from nomadx.src.infrastructure.workers.ar_event_processor import AREventProcessor
from nomadx.src.domain.receivable.value_objects import ReceivableId
from nomadx.src.domain.receivable.receivable import Receivable, ReceivableStatus
from nomadx.src.domain.receivable.collection_action import CollectionAction, CollectionActionId
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import Money, Currency, CurrencyType
from nomadx.src.domain.invoice import CustomerId, InvoiceId


class ControllableClock(Clock):
    _time = datetime(2026, 8, 2, tzinfo=timezone.utc)

    @classmethod
    def now(cls) -> datetime:
        if cls._time.tzinfo is None:
            cls._time = cls._time.replace(tzinfo=timezone.utc)
        return cls._time

    @classmethod
    def set_time(cls, new_time: datetime):
        if new_time.tzinfo is None:
            new_time = new_time.replace(tzinfo=timezone.utc)
        cls._time = new_time


@pytest.fixture(autouse=True)
def patch_clock(monkeypatch):
    monkeypatch.setattr(Clock, "now", ControllableClock.now)


async def _create_invoice_and_receivable(session: AsyncSession, org_id: str, due_date: datetime) -> str:
    cust_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    
    invoice_model = InvoiceDbModel(
        id=inv_id,
        organization_id=org_id,
        invoice_number=f"INV-{str(uuid4())[:6]}",
        status="ISSUED",
        origin_type="DIRECT",
        source_system="NomadX",
        external_invoice_id=None,
        customer_id=cust_id,
        billing_name="Test Customer",
        billing_email="customer@example.com",
        billing_address=None,
        billing_country=None,
        billing_tax_identifier=None,
        currency_code="USDC",
        currency_type="DIGITAL_ASSET",
        subtotal="100.00",
        discount_total="0.00",
        charge_total="0.00",
        tax_total="0.00",
        grand_total="100.00",
        payment_terms_type="IMMEDIATE",
        due_at=due_date,
        issued_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc)
    )
    session.add(invoice_model)

    from nomadx.src.domain.receivable.dunning import DunningState, DunningStage
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(cust_id),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("100.00"), currency),
        due_date=due_date,
        occurred_on=datetime(2026, 7, 20, tzinfo=timezone.utc)
    ).value
    receivable._props["status"] = "OVERDUE"
    receivable._props["dunning_state"] = DunningState(
        stage=DunningStage.NONE,
        attempts=0,
        last_attempt_at=None,
        next_attempt_at=due_date
    )
    
    receivable_repo = PostgresReceivableRepository(session)
    await receivable_repo.save(receivable)
    await session.commit()
    return rec_id


@pytest.mark.asyncio
async def test_dunning_paused_behavior(db_session: AsyncSession):
    org_id = str(uuid4())
    due_date = datetime(2026, 8, 1, tzinfo=timezone.utc)
    rec_id = await _create_invoice_and_receivable(db_session, org_id, due_date)

    # 1. Pause dunning on the receivable
    async with session_manager.session() as session:
        receivable_repo = PostgresReceivableRepository(session)
        receivable = await receivable_repo.get_by_id(ReceivableId(rec_id))
        receivable.pause_dunning()
        await receivable_repo.save(receivable)
        await session.commit()

    # 2. Run DunningWorker - should not progress the paused receivable
    ControllableClock.set_time(datetime(2026, 8, 5, tzinfo=timezone.utc))
    worker = DunningWorker(DunningPolicy())
    processed = await worker.process_dunning()
    assert processed == 0

    # 3. Verify stage remains initial / OPEN (or unchanged)
    async with session_manager.session() as session:
        receivable_repo = PostgresReceivableRepository(session)
        rec = await receivable_repo.get_by_id(ReceivableId(rec_id))
        assert rec.dunning_paused is True
        assert rec.dunning_state.stage.value == "NONE"


@pytest.mark.asyncio
async def test_duplicate_prevention_and_one_action_per_stage(db_session: AsyncSession):
    org_id = str(uuid4())
    due_date = datetime(2026, 8, 1, tzinfo=timezone.utc)
    rec_id = await _create_invoice_and_receivable(db_session, org_id, due_date)

    ControllableClock.set_time(datetime(2026, 8, 5, tzinfo=timezone.utc))
    worker = DunningWorker(DunningPolicy())
    
    # Run once - should advance stage and schedule action
    processed = await worker.process_dunning()
    assert processed == 1

    # Run again concurrently/immediately - should not schedule another action due to idempotency key / stage check
    processed_again = await worker.process_dunning()
    assert processed_again == 0

    # Check database records
    async with session_manager.session() as session:
        stmt = select(ReceivableCollectionActionDbModel).where(
            ReceivableCollectionActionDbModel.receivable_id == rec_id
        )
        res = await session.execute(stmt)
        actions = res.scalars().all()
        assert len(actions) == 1


@pytest.mark.asyncio
async def test_worker_crash_recovery(db_session: AsyncSession):
    org_id = str(uuid4())
    rec_id = str(uuid4())

    # Create stuck collection action in PROCESSING status
    stuck_action = ReceivableCollectionActionDbModel(
        id=str(uuid4()),
        organization_id=org_id,
        receivable_id=rec_id,
        action_type="SEND_REMINDER",
        scheduled_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        status="PROCESSING",
        attempt_count=0,
        idempotency_key=f"stuck-{str(uuid4())}",
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc)
    )
    db_session.add(stuck_action)

    # Create stuck outbox event in PROCESSING status
    stuck_event = OutboxEventDbModel(
        id=str(uuid4()),
        event_type="PaymentReceived",
        payload=json.dumps({"invoice_id": "inv-1", "allocated_amount": "100.00", "currency": "USDC"}),
        processed=False,
        status="PROCESSING",
        attempts=0,
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc)
    )
    db_session.add(stuck_event)
    await db_session.commit()

    # Run recovery on workers
    action_worker = CollectionActionWorker(None, None)
    recovered_actions = await action_worker.recover_stuck_actions(threshold_minutes=5)
    assert recovered_actions == 1

    event_processor = AREventProcessor()
    recovered_events = await event_processor.recover_stuck_events(threshold_minutes=5)
    assert recovered_events == 1

    # Verify state transitions
    async with session_manager.session() as session:
        act = (await session.execute(
            select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.id == stuck_action.id)
        )).scalar_one()
        assert act.status == "RETRYING"
        assert act.attempt_count == 1

        ev = (await session.execute(
            select(OutboxEventDbModel).where(OutboxEventDbModel.id == stuck_event.id)
        )).scalar_one()
        assert ev.status == "RETRYING"
        assert ev.attempts == 1


@pytest.mark.asyncio
async def test_retry_backoff_and_dlq_exhaustion(db_session: AsyncSession):
    org_id = str(uuid4())
    due_date = datetime(2026, 8, 1, tzinfo=timezone.utc)
    rec_id = await _create_invoice_and_receivable(db_session, org_id, due_date)

    # Create collection action
    action = ReceivableCollectionActionDbModel(
        id=str(uuid4()),
        organization_id=org_id,
        receivable_id=rec_id,
        action_type="SEND_REMINDER",
        scheduled_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        status="PENDING",
        attempt_count=0,
        idempotency_key=f"retry-{str(uuid4())}",
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc)
    )
    db_session.add(action)
    await db_session.commit()

    # We mock recipient resolver to raise transient exception
    class FailingResolver:
        async def resolve_recipient(self, *args, **kwargs):
            raise ConnectionError("Transient network timeout.")

    class EmptyTemplateService:
        async def render(self, *args, **kwargs):
            from nomadx.src.domain.shared.result.result import Result
            return Result.ok("Test Body")

    worker = CollectionActionWorker(FailingResolver(), EmptyTemplateService())

    # Exec 1: transitions to RETRYING, attempt_count=1
    processed = await worker.process_actions()
    assert processed == 0

    async with session_manager.session() as session:
        act = (await session.execute(
            select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.id == action.id)
        )).scalar_one()
        assert act.status == "RETRYING"
        assert act.attempt_count == 1
        assert act.next_retry_at is not None

        # Reset next_retry_at to past to force next run
        act.next_retry_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        await session.commit()

    # Exec 2: transitions to RETRYING, attempt_count=2
    await worker.process_actions()
    async with session_manager.session() as session:
        act = (await session.execute(
            select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.id == action.id)
        )).scalar_one()
        assert act.status == "RETRYING"
        assert act.attempt_count == 2
        act.next_retry_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        await session.commit()

    # Exec 3: reaches max_attempts (3), transitions to DEAD_LETTERED
    await worker.process_actions()
    async with session_manager.session() as session:
        act = (await session.execute(
            select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.id == action.id)
        )).scalar_one()
        assert act.status == "DEAD_LETTERED"
        assert act.attempt_count == 3
        assert act.dead_lettered_at is not None


@pytest.mark.asyncio
async def test_manual_controls_and_audit_api_routes(db_session: AsyncSession):
    email = f"admin-{str(uuid4())[:8]}@nomadx.com"
    org_slug = f"org-{str(uuid4())[:8]}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Onboard admin
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Admin User",
            "organization_name": "Reliability Corp",
            "organization_slug": org_slug,
            "default_currency": "USDC"
        })
        assert reg.status_code == 201

        login = await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })
        token = login.json()["token"]
        auth_headers = {"Authorization": f"Bearer {token}"}

        # Get secure context org ID
        ctx_resp = await ac.get("/api/secure-context", headers=auth_headers)
        org_id = ctx_resp.json()["organization_id"]

        # Create overdue receivable
        due_date = datetime(2026, 8, 1, tzinfo=timezone.utc)
        rec_id = await _create_invoice_and_receivable(db_session, org_id, due_date)

        # 1. Test Pause Dunning API
        pause_resp = await ac.post(f"/api/receivables/{rec_id}/dunning/pause", headers=auth_headers)
        assert pause_resp.status_code == 200
        assert pause_resp.json()["status"] == "success"

        # Check audit log in DB
        async with session_manager.session() as session:
            audit = (await session.execute(
                select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "DunningPaused")
            )).scalars().all()
            assert len(audit) >= 1

            rec = (await session.execute(
                select(ReceivableDbModel).where(ReceivableDbModel.id == rec_id)
            )).scalar_one()
            assert rec.dunning_paused is True

        # 2. Test Resume Dunning API
        resume_resp = await ac.post(f"/api/receivables/{rec_id}/dunning/resume", headers=auth_headers)
        assert resume_resp.status_code == 200

        async with session_manager.session() as session:
            audit = (await session.execute(
                select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "DunningResumed")
            )).scalars().all()
            assert len(audit) >= 1

            rec = (await session.execute(
                select(ReceivableDbModel).where(ReceivableDbModel.id == rec_id)
            )).scalar_one()
            assert rec.dunning_paused is False

        # Create DLQ / dead-lettered collection action
        action_id = str(uuid4())
        async with session_manager.session() as session:
            dlq_act = ReceivableCollectionActionDbModel(
                id=action_id,
                organization_id=org_id,
                receivable_id=rec_id,
                action_type="SEND_REMINDER",
                scheduled_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                status="DEAD_LETTERED",
                attempt_count=3,
                dead_lettered_at=Clock.now().replace(tzinfo=None),
                idempotency_key=f"dlq-key-{action_id}",
                created_at=datetime(2026, 8, 1, tzinfo=timezone.utc)
            )
            session.add(dlq_act)
            await session.commit()

        # 3. Query Dead Letter actions API
        dlq_list = await ac.get("/api/receivables/collection-actions/dead-letter", headers=auth_headers)
        assert dlq_list.status_code == 200
        assert len(dlq_list.json()["data"]) >= 1

        # 4. Query metrics dashboard
        metrics = await ac.get("/api/receivables/collection-actions/metrics", headers=auth_headers)
        assert metrics.status_code == 200
        assert metrics.json()["data"]["dead_lettered_actions"] >= 1

        # 5. Test Retry Collection Action API
        retry_act = await ac.post(f"/api/receivables/collection-actions/{action_id}/retry", headers=auth_headers)
        assert retry_act.status_code == 200

        async with session_manager.session() as session:
            act = (await session.execute(
                select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.id == action_id)
            )).scalar_one()
            assert act.status == "PENDING"
            assert act.attempt_count == 0

            audit = (await session.execute(
                select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "CollectionActionRetried")
            )).scalars().all()
            assert len(audit) >= 1
