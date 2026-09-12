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
    InvoiceDbModel,
    NotificationDbModel,
    WorkflowExecutionDbModel
)
from nomadx.src.infrastructure.database.repositories import (
    PostgresReceivableRepository,
    PostgresInvoiceRepository,
    PostgresCollectionActionRepository,
    current_correlation_id,
    current_causation_id
)
from nomadx.src.application.receivable.dunning_policy import DunningPolicy
from nomadx.src.infrastructure.workers.dunning_worker import DunningWorker
from nomadx.src.infrastructure.workers.collection_action_worker import CollectionActionWorker
from nomadx.src.infrastructure.workers.ar_event_processor import AREventProcessor
from nomadx.src.domain.receivable.value_objects import ReceivableId
from nomadx.src.domain.receivable.receivable import Receivable, ReceivableStatus
from nomadx.src.domain.receivable.dunning import DunningStage, DunningState
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import Money, Currency, CurrencyType
from nomadx.src.domain.invoice import CustomerId, InvoiceId


class E2EControllableClock(Clock):
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
    monkeypatch.setattr(Clock, "now", E2EControllableClock.now)


async def _seed_invoice(session: AsyncSession, org_id: str, inv_id: str, due_date: datetime) -> InvoiceDbModel:
    cust_id = str(uuid4())
    invoice_model = InvoiceDbModel(
        id=inv_id,
        organization_id=org_id,
        invoice_number=f"INV-{str(uuid4())[:8]}",
        status="ISSUED",
        origin_type="NATIVE",
        source_system="NomadX",
        external_invoice_id=None,
        customer_id=cust_id,
        billing_name="E2E Customer",
        billing_email="customer-e2e@example.com",
        billing_address="123 Test St",
        billing_country="US",
        billing_tax_identifier=None,
        currency_code="USDC",
        currency_type="DIGITAL_ASSET",
        subtotal="1000.00",
        discount_total="0.00",
        charge_total="0.00",
        tax_total="0.00",
        grand_total="1000.00",
        payment_terms_type="DUE_ON_RECEIPT",
        due_at=due_date,
        issued_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc)
    )
    session.add(invoice_model)
    await session.commit()
    return invoice_model


@pytest.mark.asyncio
async def test_invoice_creation_and_duplicate_idempotency(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    due_date = datetime(2030, 1, 1, tzinfo=timezone.utc)
    await _seed_invoice(db_session, org_id, inv_id, due_date)

    # 1. Create InvoiceIssued outbox event
    issued_event = OutboxEventDbModel(
        id=str(uuid4()),
        event_type="InvoiceIssued",
        payload=json.dumps({
            "invoice_id": inv_id,
            "organization_id": org_id,
            "occurred_on": datetime(2026, 7, 20, tzinfo=timezone.utc).isoformat()
        }),
        processed=False,
        status="PENDING",
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc)
    )
    db_session.add(issued_event)
    await db_session.commit()

    # 2. Run AREventProcessor - should consume event and create Receivable
    processor = AREventProcessor()
    processed = await processor.process_payment_events()
    assert processed == 1

    # Verify Receivable exists in DB
    async with session_manager.session() as session:
        receivable_repo = PostgresReceivableRepository(session)
        rec = await receivable_repo.find_by_invoice(InvoiceId(inv_id))
        assert rec is not None
        assert rec.status == ReceivableStatus.OPEN
        assert rec.outstanding_amount.amount == Decimal("1000.00")

    # 3. Simulate DUPLICATE InvoiceIssued event
    duplicate_event = OutboxEventDbModel(
        id=str(uuid4()),
        event_type="InvoiceIssued",
        payload=json.dumps({
            "invoice_id": inv_id,
            "organization_id": org_id,
            "occurred_on": datetime(2026, 7, 20, tzinfo=timezone.utc).isoformat()
        }),
        processed=False,
        status="PENDING",
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc)
    )
    db_session.add(duplicate_event)
    await db_session.commit()

    # Run processor again - duplicate should be processed and skipped harmlessly
    processed_again = await processor.process_payment_events()
    assert processed_again == 1

    # Assert only one Receivable exists
    async with session_manager.session() as session:
        res = await session.execute(
            select(ReceivableDbModel).where(ReceivableDbModel.invoice_id == inv_id)
        )
        receivables = res.scalars().all()
        assert len(receivables) == 1


@pytest.mark.asyncio
async def test_payment_allocation_partial_full_and_reversals(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    due_date = datetime(2030, 1, 1, tzinfo=timezone.utc)
    await _seed_invoice(db_session, org_id, inv_id, due_date)

    # Setup Receivable
    processor = AREventProcessor()
    issued_event = OutboxEventDbModel(
        id=str(uuid4()),
        event_type="InvoiceIssued",
        payload=json.dumps({"invoice_id": inv_id, "organization_id": org_id}),
        processed=False,
        status="PENDING",
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc)
    )
    db_session.add(issued_event)
    await db_session.commit()
    await processor.process_payment_events()

    # 1. Partial Payment event: $400.00
    payment_id_1 = str(uuid4())
    event_partial = OutboxEventDbModel(
        id=str(uuid4()),
        event_type="PaymentReceived",
        payload=json.dumps({
            "payment_id": payment_id_1,
            "invoice_id": inv_id,
            "amount": "400.00",
            "currency": "USDC",
            "organization_id": org_id
        }),
        processed=False,
        status="PENDING",
        created_at=datetime(2026, 8, 5, tzinfo=timezone.utc)
    )
    db_session.add(event_partial)
    await db_session.commit()

    await processor.process_payment_events()

    async with session_manager.session() as session:
        receivable_repo = PostgresReceivableRepository(session)
        rec = await receivable_repo.find_by_invoice(InvoiceId(inv_id))
        assert rec.status == ReceivableStatus.PARTIALLY_PAID
        assert rec.outstanding_amount.amount == Decimal("600.00")

    # 2. Overpayment and Idempotency: $700.00 (Outstanding is $600.00, so $100.00 is excess)
    payment_id_2 = str(uuid4())
    event_over = OutboxEventDbModel(
        id=str(uuid4()),
        event_type="PaymentReceived",
        payload=json.dumps({
            "payment_id": payment_id_2,
            "invoice_id": inv_id,
            "amount": "700.00",
            "currency": "USDC",
            "organization_id": org_id
        }),
        processed=False,
        status="PENDING",
        created_at=datetime(2026, 8, 6, tzinfo=timezone.utc)
    )
    db_session.add(event_over)
    await db_session.commit()

    await processor.process_payment_events()

    async with session_manager.session() as session:
        receivable_repo = PostgresReceivableRepository(session)
        rec = await receivable_repo.find_by_invoice(InvoiceId(inv_id))
        assert rec.status == ReceivableStatus.SETTLED
        assert rec.outstanding_amount.amount == Decimal("0.00")
        # Ensure outstanding balance never dips below zero
        assert rec.allocated_amount.amount == Decimal("1000.00")

    # Replay same payment event (idempotency check)
    async with session_manager.session() as session:
        ev = (await session.execute(
            select(OutboxEventDbModel).where(OutboxEventDbModel.id == event_over.id)
        )).scalar_one()
        ev.processed = False
        ev.status = "PENDING"
        await session.commit()

    await processor.process_payment_events()
    async with session_manager.session() as session:
        receivable_repo = PostgresReceivableRepository(session)
        rec = await receivable_repo.find_by_invoice(InvoiceId(inv_id))
        assert rec.outstanding_amount.amount == Decimal("0.00")
        assert rec.allocated_amount.amount == Decimal("1000.00")

    # 3. Payment Reversed event: reverses the $700.00 allocation (bringing outstanding back to $600.00)
    event_reverse = OutboxEventDbModel(
        id=str(uuid4()),
        event_type="PaymentReversed",
        payload=json.dumps({
            "payment_id": payment_id_2,
            "invoice_id": inv_id,
            "amount": "600.00", # Reverses the allocated portion
            "currency": "USDC",
            "organization_id": org_id
        }),
        processed=False,
        status="PENDING",
        created_at=datetime(2026, 8, 7, tzinfo=timezone.utc)
    )
    db_session.add(event_reverse)
    await db_session.commit()

    await processor.process_payment_events()

    async with session_manager.session() as session:
        receivable_repo = PostgresReceivableRepository(session)
        rec = await receivable_repo.find_by_invoice(InvoiceId(inv_id))
        assert rec.status == ReceivableStatus.PARTIALLY_PAID
        assert rec.outstanding_amount.amount == Decimal("600.00")


@pytest.mark.asyncio
async def test_cross_tenant_isolation(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())

    inv_a = str(uuid4())
    inv_b = str(uuid4())

    await _seed_invoice(db_session, org_a, inv_a, datetime(2026, 8, 1, tzinfo=timezone.utc))
    await _seed_invoice(db_session, org_b, inv_b, datetime(2026, 8, 1, tzinfo=timezone.utc))

    processor = AREventProcessor()
    
    # Create InvoiceIssued events for both orgs
    evt_a = OutboxEventDbModel(id=str(uuid4()), event_type="InvoiceIssued", payload=json.dumps({"invoice_id": inv_a, "organization_id": org_a}))
    evt_b = OutboxEventDbModel(id=str(uuid4()), event_type="InvoiceIssued", payload=json.dumps({"invoice_id": inv_b, "organization_id": org_b}))
    db_session.add(evt_a)
    db_session.add(evt_b)
    await db_session.commit()

    await processor.process_payment_events()

    # Validate that both receivables exist and have correct organization mapping
    async with session_manager.session() as session:
        receivable_repo = PostgresReceivableRepository(session)
        rec_a = await receivable_repo.find_by_invoice(InvoiceId(inv_a))
        rec_b = await receivable_repo.find_by_invoice(InvoiceId(inv_b))
        
        assert rec_a.organization_id.value == org_a
        assert rec_b.organization_id.value == org_b


@pytest.mark.asyncio
async def test_complete_successful_business_journey(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    due_date = datetime(2026, 8, 1, tzinfo=timezone.utc)
    await _seed_invoice(db_session, org_id, inv_id, due_date)

    # Step 1: Invoice Issued -> Receivable Created
    processor = AREventProcessor()
    
    issued_event = OutboxEventDbModel(
        id=str(uuid4()),
        event_type="InvoiceIssued",
        payload=json.dumps({
            "invoice_id": inv_id,
            "organization_id": org_id,
            "occurred_on": datetime(2026, 7, 20, tzinfo=timezone.utc).isoformat()
        }),
        processed=False,
        status="PENDING",
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc)
    )
    db_session.add(issued_event)
    await db_session.commit()

    await processor.process_payment_events()

    # Verify event lineage correlation and causation IDs propagated to outbox
    async with session_manager.session() as session:
        outbox = (await session.execute(
            select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "ReceivableCreated")
        )).scalar_one()
        payload = json.loads(outbox.payload)
        metadata = payload.get("_metadata", {})
        assert metadata.get("correlation_id") == issued_event.id
        assert metadata.get("causation_id") == issued_event.id

    # Step 2: Invoice Becomes Due -> Receivable Becomes Overdue
    E2EControllableClock.set_time(datetime(2026, 8, 5, tzinfo=timezone.utc))

    # Initialize next_attempt_at so dunning worker targets it
    async with session_manager.session() as session:
        receivable_repo = PostgresReceivableRepository(session)
        rec = await receivable_repo.find_by_invoice(InvoiceId(inv_id))
        rec._props["dunning_state"] = DunningState(
            stage=DunningStage.NONE,
            attempts=0,
            last_attempt_at=None,
            next_attempt_at=datetime(2026, 8, 1, tzinfo=timezone.utc)
        )
        await receivable_repo.save(rec)
        await session.commit()

    policy = DunningPolicy()
    dunning_worker = DunningWorker(policy)
    processed_dunning = await dunning_worker.process_dunning()
    assert processed_dunning == 1

    # Assert dunning stage changed and collection action scheduled
    async with session_manager.session() as session:
        stmt = select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.receivable_id == rec.id.value)
        actions = (await session.execute(stmt)).scalars().all()
        assert len(actions) == 1
        action = actions[0]
        assert action.status == "PENDING"
        assert action.action_type == "SEND_REMINDER"


        
    # Step 4: Customer Pays -> Payment Received
    payment_id = str(uuid4())
    pay_event = OutboxEventDbModel(
        id=str(uuid4()),
        event_type="PaymentReceived",
        payload=json.dumps({
            "payment_id": payment_id,
            "invoice_id": inv_id,
            "amount": "1000.00",
            "currency": "USDC",
            "organization_id": org_id
        }),
        processed=False,
        status="PENDING",
        created_at=datetime(2026, 8, 6, tzinfo=timezone.utc)
    )
    db_session.add(pay_event)
    await db_session.commit()

    # Step 5: Run event processor to allocate payment
    await processor.process_payment_events()

    # Assert Receivable is settled and pending collection actions cancelled
    async with session_manager.session() as session:
        receivable_repo = PostgresReceivableRepository(session)
        rec = await receivable_repo.find_by_invoice(InvoiceId(inv_id))
        assert rec.status == ReceivableStatus.SETTLED

        # Collection action should have been transitioned to CANCELLED
        action_stmt = select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.id == action.id)
        act = (await session.execute(action_stmt)).scalar_one()
        assert act.status == "CANCELLED"


@pytest.mark.asyncio
async def test_complete_failure_and_recovery_journey(db_session: AsyncSession):
    email = f"e2e_admin_{uuid4()}@nomadx.com"
    org_slug = f"e2e-org-{uuid4()}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register and login to target secure context APIs
        await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner User",
            "organization_name": "Failure Journey Corp",
            "organization_slug": org_slug,
            "default_currency": "USD"
        })
        login_res = await ac.post("/auth/login", json={"email": email, "password": "strongpassword123"})
        token = login_res.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Retrieve organization ID
        ctx_res = await ac.get("/api/secure-context", headers=headers)
        org_id = ctx_res.json()["organization_id"]

        # Seed overdue receivable
        inv_id = str(uuid4())
        await _seed_invoice(db_session, org_id, inv_id, datetime(2026, 8, 1, tzinfo=timezone.utc))

        processor = AREventProcessor()
        issued_event = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="InvoiceIssued",
            payload=json.dumps({"invoice_id": inv_id, "organization_id": org_id}),
            processed=False,
            status="PENDING",
            created_at=datetime(2026, 7, 20, tzinfo=timezone.utc)
        )
        db_session.add(issued_event)
        await db_session.commit()
        await processor.process_payment_events()

        # Update dunning state next_attempt_at for worker target selection
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            rec = await receivable_repo.find_by_invoice(InvoiceId(inv_id))
            rec._props["dunning_state"] = DunningState(
                stage=DunningStage.NONE,
                attempts=0,
                last_attempt_at=None,
                next_attempt_at=datetime(2026, 8, 1, tzinfo=timezone.utc)
            )
            await receivable_repo.save(rec)
            await session.commit()

        # Run dunning worker
        E2EControllableClock.set_time(datetime(2026, 8, 5, tzinfo=timezone.utc))
        policy = DunningPolicy()
        dunning_worker = DunningWorker(policy)
        await dunning_worker.process_dunning()

        # Get the scheduled action ID
        async with session_manager.session() as session:
            action = (await session.execute(
                select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.receivable_id == rec.id.value)
            )).scalar_one()
            action_id = action.id

        # 1. Simulate Notification delivery failure (raises Transient Connection Error)
        class TransientFailingResolver:
            async def resolve_recipient(self, *args, **kwargs):
                raise ConnectionError("Mail provider timeout.")

        class EmptyTemplate:
            async def render(self, *args, **kwargs):
                from nomadx.src.domain.shared.result.result import Result
                return Result.ok("")

        action_worker = CollectionActionWorker(TransientFailingResolver(), EmptyTemplate())
        
        # Exec 1: RETRYING
        await action_worker.process_actions()
        async with session_manager.session() as session:
            act = (await session.execute(
                select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.id == action_id)
            )).scalar_one()
            assert act.status == "RETRYING"
            # Reset next_retry_at to force immediate execution
            act.next_retry_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
            await session.commit()

        # Exec 2: RETRYING (attempt_count=2)
        await action_worker.process_actions()
        async with session_manager.session() as session:
            act = (await session.execute(
                select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.id == action_id)
            )).scalar_one()
            assert act.status == "RETRYING"
            act.next_retry_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
            await session.commit()

        # Exec 3: DLQ (DEAD_LETTERED)
        await action_worker.process_actions()
        async with session_manager.session() as session:
            act = (await session.execute(
                select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.id == action_id)
            )).scalar_one()
            assert act.status == "DEAD_LETTERED"

        # 2. Trigger Admin Intervention: Retry collection action route
        retry_res = await ac.post(f"/api/receivables/collection-actions/{action_id}/retry", headers=headers)
        assert retry_res.status_code == 200

        # Verify reset to PENDING and attempt_count reset
        async with session_manager.session() as session:
            act = (await session.execute(
                select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.id == action_id)
            )).scalar_one()
            assert act.status == "PENDING"
            assert act.attempt_count == 0

        # 3. Successful Replay (with working resolver mock)
        class WorkingResolver:
            async def resolve_recipient(self, *args, **kwargs):
                return "resolved-merchant@example.com"

        successful_worker = CollectionActionWorker(WorkingResolver(), EmptyTemplate())
        processed = await successful_worker.process_actions()
        assert processed == 1

        # Confirm completion status
        async with session_manager.session() as session:
            act = (await session.execute(
                select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.id == action_id)
            )).scalar_one()
            assert act.status == "COMPLETED"
