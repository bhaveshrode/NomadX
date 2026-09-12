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
from nomadx.src.infrastructure.database.models import (
    ReceivableDbModel,
    ReceivableCollectionActionDbModel,
    OutboxEventDbModel
)
from nomadx.src.infrastructure.database.repositories import PostgresReceivableRepository, PostgresCollectionActionRepository
from nomadx.src.infrastructure.workers.dunning_worker import DunningWorker
from nomadx.src.infrastructure.workers.collection_action_worker import CollectionActionWorker
from nomadx.src.domain.receivable.value_objects import ReceivableId
from nomadx.src.domain.receivable.receivable import Receivable
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import Money, Currency, CurrencyType
from nomadx.src.domain.invoice import CustomerId, InvoiceId
from nomadx.src.application.receivable.dunning_policy import DunningPolicy
from nomadx.src.domain.receivable.dunning import DunningStage


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


@pytest.mark.asyncio
async def test_dunning_lifecycle_and_actions(db_session: AsyncSession):
    org_id = str(uuid4())
    cust_id = str(uuid4())
    inv_id = str(uuid4())
    rec_id = str(uuid4())

    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    receivable_repo = PostgresReceivableRepository(db_session)

    from nomadx.src.infrastructure.database.models import InvoiceDbModel
    invoice_model = InvoiceDbModel(
        id=inv_id,
        organization_id=org_id,
        invoice_number="INV-1234",
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
        subtotal="10000.00",
        discount_total="0.00",
        charge_total="0.00",
        tax_total="0.00",
        grand_total="10000.00",
        payment_terms_type="IMMEDIATE",
        due_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        issued_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc)
    )
    db_session.add(invoice_model)

    # 1. Create a Receivable that is already Overdue
    ControllableClock.set_time(datetime(2026, 8, 5, tzinfo=timezone.utc))
    receivable = Receivable.create(
        id=ReceivableId(rec_id),
        organization_id=OrganizationId(org_id),
        customer_id=CustomerId(cust_id),
        invoice_id=InvoiceId(inv_id),
        original_amount=Money(Decimal("10000.00"), currency),
        due_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
        occurred_on=datetime(2026, 7, 20, tzinfo=timezone.utc)
    ).value
    receivable._props["status"] = "OVERDUE"
    from nomadx.src.domain.receivable.dunning import DunningState
    receivable._props["dunning_state"] = DunningState(
        stage=DunningStage.NONE,
        attempts=0,
        last_attempt_at=None,
        next_attempt_at=datetime(2026, 8, 5, tzinfo=timezone.utc)
    )
    await receivable_repo.save(receivable)
    await db_session.commit()

    # Debug log database rows to check status and date formatting
    async with session_manager.session() as check_session:
        res = await check_session.execute(select(ReceivableDbModel))
        rows = res.scalars().all()
        print("\n=== DEBUG DATABASE ROWS ===")
        for r in rows:
            print(f"ROW: id={r.id}, status={r.status}, next_dunning={r.next_dunning_at}, outstanding={r.outstanding_amount}")
        print(f"CLOCK NOW: {ControllableClock.now()}\n")

    # 2. Run DunningWorker to transition NONE -> FIRST_REMINDER
    policy = DunningPolicy()
    dunning_worker = DunningWorker(policy)
    count = await dunning_worker.process_dunning()
    assert count == 1

    # Verify transition
    async with session_manager.session() as check_session:
        db_stmt = select(ReceivableDbModel).where(ReceivableDbModel.id == rec_id)
        db_rec = (await check_session.execute(db_stmt)).scalar_one()
        assert db_rec.dunning_stage == "FIRST_REMINDER"
        assert db_rec.dunning_attempt_count == 1

        # Verify collection action scheduled
        stmt = select(ReceivableCollectionActionDbModel).where(
            ReceivableCollectionActionDbModel.receivable_id == rec_id,
            ReceivableCollectionActionDbModel.status == "PENDING"
        )
        actions = (await check_session.execute(stmt)).scalars().all()
        assert len(actions) == 1
        assert actions[0].action_type == "SEND_REMINDER"

    # 3. Run CollectionActionWorker to process scheduled actions
    from nomadx.src.application.notification.recipient_service import RecipientResolver
    from nomadx.src.application.notification.template_service import NotificationTemplateService
    from nomadx.src.infrastructure.database.repositories import (
        PostgresNotificationPreferenceRepository,
        PostgresNotificationTemplateRepository
    )

    async with session_manager.session() as check_session:
        pref_repo = PostgresNotificationPreferenceRepository(check_session)
        resolver = RecipientResolver()
        tmpl_repo = PostgresNotificationTemplateRepository(check_session)
        tmpl_svc = NotificationTemplateService(tmpl_repo)
        
        async def mock_resolve(*args, **kwargs):
            return "customer@example.com"
        resolver.resolve_recipient = mock_resolve
        
        action_worker = CollectionActionWorker(resolver, tmpl_svc)
        class FakeResult:
            is_success = True
            value = "Dunning Notice Content"
        async def mock_render(*args, **kwargs):
            return FakeResult()
        tmpl_svc.render = mock_render

        # Debug print collection actions
        res_actions = await check_session.execute(select(ReceivableCollectionActionDbModel))
        rows_actions = res_actions.scalars().all()
        print("\n=== DEBUG ACTIONS BEFORE RUNNING WORKER ===")
        for ra in rows_actions:
            print(f"ACTION: id={ra.id}, status={ra.status}, scheduled_at={ra.scheduled_at}, next_retry_at={ra.next_retry_at}, key={ra.idempotency_key}")
        print(f"CLOCK NOW FOR ACTIONS: {ControllableClock.now()}\n")

        repo = PostgresCollectionActionRepository(check_session)
        acts = await repo.claim_pending_actions(10, ControllableClock.now())
        print(f"DIRECT CLAIM IN TEST: len={len(acts)}")
        if acts:
            print(f"DIRECT CLAIMED ACTION: id={acts[0].id.value}, status={acts[0].status}, scheduled_at={acts[0].scheduled_at}")

    processed_actions = await action_worker.process_actions()
    print(f"WORKER RETURNED: {processed_actions}")

    # Verify action completed in a new session
    async with session_manager.session() as check_session:
        res_actions2 = await check_session.execute(select(ReceivableCollectionActionDbModel))
        rows_actions2 = res_actions2.scalars().all()
        print("\n=== DEBUG ACTIONS AFTER RUNNING WORKER ===")
        for ra in rows_actions2:
            print(f"ACTION: id={ra.id}, status={ra.status}, error={ra.error_message}, attempt={ra.attempt_count}")

    assert processed_actions == 1

    # Verify action completed
    async with session_manager.session() as check_session:
        stmt = select(ReceivableCollectionActionDbModel).where(
            ReceivableCollectionActionDbModel.receivable_id == rec_id
        )
        actions = (await check_session.execute(stmt)).scalars().all()
        assert actions[0].status == "COMPLETED"


@pytest.mark.asyncio
async def test_manual_dunning_controls():
    email = f"dunning_merchant_{uuid4()}@nomadx.com"
    org_slug = f"dunning-org-{uuid4()}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register and login
        await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Merchant Owner",
            "organization_name": "Dunning Corp",
            "organization_slug": org_slug,
            "default_currency": "USD"
        })
        login_res = await ac.post("/auth/login", json={"email": email, "password": "strongpassword123"})
        token = login_res.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Get secure context organization id
        ctx_res = await ac.get("/api/secure-context", headers=headers)
        org_id = ctx_res.json()["organization_id"]

        # Seed receivable manually in database
        rec_id = str(uuid4())
        async with session_manager.session() as session:
            repo = PostgresReceivableRepository(session)
            currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
            rec = Receivable.create(
                id=ReceivableId(rec_id),
                organization_id=OrganizationId(org_id),
                customer_id=CustomerId(str(uuid4())),
                invoice_id=InvoiceId(str(uuid4())),
                original_amount=Money(Decimal("1000.00"), currency),
                due_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
                occurred_on=datetime(2026, 7, 20, tzinfo=timezone.utc)
            ).value
            rec._props["status"] = "OVERDUE"
            rec._props["next_dunning_at"] = Clock.now()
            await repo.save(rec)
            await session.commit()

        # Pause dunning
        pause_res = await ac.post(f"/api/receivables/{rec_id}/dunning/pause", headers=headers)
        assert pause_res.status_code == 200

        async with session_manager.session() as check_session:
            db_stmt = select(ReceivableDbModel).where(ReceivableDbModel.id == rec_id)
            db_rec = (await check_session.execute(db_stmt)).scalar_one()
            # Since SQLite/PostgreSQL might drop tz or persist differently, compare naive or aware correctly
            target_dt = datetime(9999, 12, 31)
            # Remove tzinfo for comparison if db returns naive
            db_dt = db_rec.next_dunning_at.replace(tzinfo=None) if db_rec.next_dunning_at else None
            assert db_dt == target_dt

        # Resume dunning
        resume_res = await ac.post(f"/api/receivables/{rec_id}/dunning/resume", headers=headers)
        assert resume_res.status_code == 200

        async with session_manager.session() as check_session:
            db_stmt = select(ReceivableDbModel).where(ReceivableDbModel.id == rec_id)
            db_rec = (await check_session.execute(db_stmt)).scalar_one()
            db_dt = db_rec.next_dunning_at.replace(tzinfo=None) if db_rec.next_dunning_at else None
            assert db_dt < target_dt
