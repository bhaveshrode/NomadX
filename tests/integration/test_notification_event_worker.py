import pytest
import json
from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.notification.value_objects import NotificationChannel
from nomadx.src.infrastructure.database.models import (
    OutboxEventDbModel,
    NotificationDbModel,
    InvoiceDbModel,
    ReceivableDbModel,
    PaymentDbModel,
    PaymentAllocationDbModel,
    SettlementDbModel,
    OrganizationDbModel,
    UserDbModel
)
from nomadx.src.infrastructure.workers.notification_event_worker import NotificationEventWorker
from nomadx.src.application.notification.recipient_service import RecipientResolver
from nomadx.src.application.notification.template_service import NotificationTemplateService
from nomadx.src.infrastructure.database.repositories import (
    PostgresNotificationRepository,
    PostgresNotificationPreferenceRepository,
    PostgresNotificationTemplateRepository
)


async def _create_test_org_and_owner(session: AsyncSession, org_id: str, owner_id: str, owner_email: str):
    owner = UserDbModel(
        id=owner_id,
        email=owner_email,
        password_hash="hash",
        name="Owner Name",
        status="ACTIVE"
    )
    org = OrganizationDbModel(
        id=org_id,
        name="Merchant Corp",
        slug=f"merchant-corp-{org_id[:8]}",
        status="ACTIVE",
        default_currency="USD",
        owner_id=owner_id
    )
    session.add(owner)
    session.add(org)
    await session.flush()


@pytest.mark.asyncio
async def test_worker_processes_invoice_issued_event(db_session: AsyncSession):
    org_id = str(uuid4())
    owner_id = str(uuid4())
    invoice_id = str(uuid4())
    
    await _create_test_org_and_owner(db_session, org_id, owner_id, "merchant@example.com")

    # Save mock invoice
    invoice = InvoiceDbModel(
        id=invoice_id,
        organization_id=org_id,
        invoice_number="INV-2026-999",
        origin_type="NATIVE",
        currency_code="USD",
        currency_type="FIAT",
        status="ISSUED",
        subtotal="100.00",
        discount_total="0.00",
        charge_total="0.00",
        tax_total="0.00",
        grand_total="100.00",
        payment_terms_type="DUE_ON_RECEIPT",
        billing_name="Alice Customer",
        billing_email="alice@customer.com"
    )
    db_session.add(invoice)

    # Save pending Outbox event for InvoiceIssued
    evt_id = str(uuid4())
    payload = {
        "invoice_id": invoice_id,
        "invoice_number": "INV-2026-999",
        "organization_id": org_id,
        "occurred_on": Clock.now().isoformat()
    }
    outbox = OutboxEventDbModel(
        id=evt_id,
        event_type="InvoiceIssued",
        payload=json.dumps(payload),
        processed=False,
        created_at=Clock.now()
    )
    db_session.add(outbox)
    await db_session.commit()

    # Wire up worker
    resolver = RecipientResolver()
    pref_repo = PostgresNotificationPreferenceRepository(db_session)
    temp_repo = PostgresNotificationTemplateRepository(db_session)
    temp_svc = NotificationTemplateService(temp_repo)
    worker = NotificationEventWorker(resolver, temp_svc, pref_repo)

    # Process events
    processed = await worker.process_pending_events()
    assert processed == 1

    # Reload outbox and check processed status
    db_session.expire_all()
    stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.id == evt_id)
    reloaded_evt = (await db_session.execute(stmt)).scalar_one()
    assert reloaded_evt.processed is True

    # Check notification created with correct email channel and recipient
    stmt_notif = select(NotificationDbModel).where(NotificationDbModel.source_event_id == evt_id)
    notif = (await db_session.execute(stmt_notif)).scalar_one()
    assert notif.organization_id == org_id
    assert notif.channel == "EMAIL"
    assert notif.recipient == "alice@customer.com"
    assert notif.status == "PENDING"


@pytest.mark.asyncio
async def test_worker_processes_settlement_completed_event(db_session: AsyncSession):
    org_id = str(uuid4())
    owner_id = str(uuid4())
    settlement_id = str(uuid4())

    await _create_test_org_and_owner(db_session, org_id, owner_id, "merchant_owner@example.com")

    settl = SettlementDbModel(
        id=settlement_id,
        organization_id=org_id,
        reference="SETL-999",
        source_payment_id=str(uuid4()),
        amount="5000.00",
        asset_code="USDC",
        asset_type="FIAT",
        asset_decimals=6,
        method="BLOCKCHAIN",
        network="POLYGON",
        destination_type="WALLET",
        destination_identifier="0xabc123",
        status="COMPLETED",
        idempotency_key="idem-settl-999",
        created_at=Clock.now(),
        updated_at=Clock.now()
    )
    db_session.add(settl)

    # Save pending Outbox event for SettlementCompleted
    evt_id = str(uuid4())
    payload = {
        "settlement_id": settlement_id,
        "organization_id": org_id,
        "reference": "SETL-999",
        "status": "COMPLETED",
        "occurred_on": Clock.now().isoformat()
    }
    outbox = OutboxEventDbModel(
        id=evt_id,
        event_type="SettlementCompleted",
        payload=json.dumps(payload),
        processed=False,
        created_at=Clock.now()
    )
    db_session.add(outbox)
    await db_session.commit()

    resolver = RecipientResolver()
    pref_repo = PostgresNotificationPreferenceRepository(db_session)
    temp_repo = PostgresNotificationTemplateRepository(db_session)
    temp_svc = NotificationTemplateService(temp_repo)
    worker = NotificationEventWorker(resolver, temp_svc, pref_repo)

    # Process events: SettlementCompleted defaults to both EMAIL and WEBHOOK channels
    processed = await worker.process_pending_events()
    assert processed == 1

    db_session.expire_all()

    # Verify both EMAIL and WEBHOOK notifications created (idempotency check covers this event run)
    stmt_notifs = select(NotificationDbModel).where(NotificationDbModel.source_event_id == evt_id)
    notifs = (await db_session.execute(stmt_notifs)).scalars().all()
    assert len(notifs) == 2
    channels = [n.channel for n in notifs]
    assert "EMAIL" in channels
    assert "WEBHOOK" in channels
    # Recipient for merchant events should resolve to organization owner email
    merchant_notif = next(n for n in notifs if n.channel == "EMAIL")
    assert merchant_notif.recipient == "merchant_owner@example.com"


@pytest.mark.asyncio
async def test_worker_event_processing_idempotency(db_session: AsyncSession):
    org_id = str(uuid4())
    owner_id = str(uuid4())
    invoice_id = str(uuid4())

    await _create_test_org_and_owner(db_session, org_id, owner_id, "merchant@example.com")

    # Mock Invoice
    invoice = InvoiceDbModel(
        id=invoice_id,
        organization_id=org_id,
        invoice_number="INV-2026-888",
        origin_type="NATIVE",
        currency_code="USD",
        currency_type="FIAT",
        status="ISSUED",
        subtotal="10.00",
        discount_total="0.00",
        charge_total="0.00",
        tax_total="0.00",
        grand_total="10.00",
        payment_terms_type="DUE_ON_RECEIPT",
        billing_name="Alice Customer",
        billing_email="alice@customer.com"
    )
    db_session.add(invoice)

    evt_id = str(uuid4())
    payload = {
        "invoice_id": invoice_id,
        "invoice_number": "INV-2026-888",
        "organization_id": org_id,
        "occurred_on": Clock.now().isoformat()
    }
    outbox = OutboxEventDbModel(
        id=evt_id,
        event_type="InvoiceIssued",
        payload=json.dumps(payload),
        processed=False,
        created_at=Clock.now()
    )
    db_session.add(outbox)
    await db_session.commit()

    resolver = RecipientResolver()
    pref_repo = PostgresNotificationPreferenceRepository(db_session)
    temp_repo = PostgresNotificationTemplateRepository(db_session)
    temp_svc = NotificationTemplateService(temp_repo)
    worker = NotificationEventWorker(resolver, temp_svc, pref_repo)

    # 1. First Run: processed = 1
    p1 = await worker.process_pending_events()
    assert p1 == 1

    # Check notification count for this event is 1
    stmt_count = select(NotificationDbModel).where(NotificationDbModel.source_event_id == evt_id)
    notifs = (await db_session.execute(stmt_count)).scalars().all()
    assert len(notifs) == 1

    # 2. Reset outbox processed status to false to simulate redelivery/restart
    # Clear stale test session map to pick up worker's commit
    db_session.expire_all()
    async with db_session.begin_nested():
        outbox.processed = False
    await db_session.commit()

    # 3. Second Run: should skip notification creation due to idempotency check
    p2 = await worker.process_pending_events()
    assert p2 == 1 # still processes (marks processed) but skips creation

    db_session.expire_all()
    notifs_after = (await db_session.execute(stmt_count)).scalars().all()
    assert len(notifs_after) == 1 # count remains 1, no duplicate notification!


@pytest.mark.asyncio
async def test_worker_transactional_rollback_on_failure(db_session: AsyncSession):
    org_id = str(uuid4())
    evt_id = str(uuid4())

    # Create Outbox event for InvoiceIssued with invalid payload data (to trigger exception during variables build)
    payload = {
        "invoice_id": None, # Will fail because invoice_id is required
        "organization_id": org_id
    }
    outbox = OutboxEventDbModel(
        id=evt_id,
        event_type="InvoiceIssued",
        payload=json.dumps(payload),
        processed=False,
        created_at=Clock.now()
    )
    db_session.add(outbox)
    await db_session.commit()

    # Create broken resolver that raises exception
    class BrokenRecipientResolver(RecipientResolver):
        async def resolve_recipient(self, session, org_id, recipient_type, channel, context_ids):
            raise ValueError("Database Connection Interrupted.")

    resolver = BrokenRecipientResolver()
    pref_repo = PostgresNotificationPreferenceRepository(db_session)
    temp_repo = PostgresNotificationTemplateRepository(db_session)
    temp_svc = NotificationTemplateService(temp_repo)
    worker = NotificationEventWorker(resolver, temp_svc, pref_repo)

    # Execute worker - should raise exception and roll back outbox processed state
    with pytest.raises(ValueError, match="Database Connection Interrupted."):
        await worker.process_pending_events()

    db_session.expire_all()

    # Verify that the outbox event is STILL unprocessed (rollback worked)
    stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.id == evt_id)
    reloaded_evt = (await db_session.execute(stmt)).scalar_one()
    assert reloaded_evt.processed is False
