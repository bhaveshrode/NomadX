import pytest
from uuid import uuid4
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.src.infrastructure.database.models import NotificationDbModel, OutboxEventDbModel
from nomadx.src.infrastructure.database.repositories import PostgresNotificationRepository
from nomadx.src.domain.notification.notification import Notification, NotificationStatus
from nomadx.src.domain.notification.value_objects import (
    NotificationId,
    NotificationReference,
    NotificationRecipient,
    NotificationChannel,
    NotificationType,
    NotificationPriority,
    NotificationTemplateId,
    NotificationPayload,
    NotificationFailure,
    NotificationFailureCode,
    NotificationIdempotencyKey
)
from nomadx.src.domain.organization.value_objects import OrganizationId


def _create_base_params(org_id: str, channel: NotificationChannel, destination: str, reference: str, idempotency_key: str):
    return {
        "id": NotificationId(str(uuid4())),
        "organization_id": OrganizationId(org_id),
        "reference": NotificationReference(reference),
        "notification_type": NotificationType.INVOICE_ISSUED,
        "recipient": NotificationRecipient(channel, destination),
        "channel": channel,
        "subject": "Invoice issued successfully",
        "template_id": NotificationTemplateId("invoice_issued"),
        "payload": NotificationPayload({"customer_name": "Bob", "amount": "50.00"}),
        "priority": NotificationPriority.NORMAL,
        "idempotency_key": NotificationIdempotencyKey(idempotency_key),
        "source_event_id": str(uuid4()),
        "source_event_type": "InvoiceIssued"
    }


@pytest.mark.asyncio
async def test_notification_create_save_retrieve_reconstruction(db_session: AsyncSession):
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.EMAIL, "bob@example.com", "NOT-2026-001001", "idem-key-1")

    repo = PostgresNotificationRepository(db_session)
    notification = Notification.create(**params).value
    await repo.save(notification)
    await db_session.flush()

    # Retrieve and Reconstruct
    retrieved = await repo.get_by_id(params["id"])
    
    assert retrieved is not None
    assert retrieved.id == params["id"]
    assert retrieved.organization_id == params["organization_id"]
    assert retrieved.reference.value == "NOT-2026-001001"
    assert retrieved.channel == NotificationChannel.EMAIL
    assert retrieved.recipient.destination == "bob@example.com"
    assert retrieved.subject == "Invoice issued successfully"
    assert retrieved.template_id.value == "invoice_issued"
    assert retrieved.payload.data == {"customer_name": "Bob", "amount": "50.00"}
    assert retrieved.priority == NotificationPriority.NORMAL
    assert retrieved.status == NotificationStatus.PENDING
    assert retrieved.idempotency_key.value == "idem-key-1"
    assert retrieved.source_event_id == params["source_event_id"]
    assert retrieved.source_event_type == "InvoiceIssued"


@pytest.mark.asyncio
async def test_notification_lifecycle_persistence(db_session: AsyncSession):
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.EMAIL, "bob@example.com", "NOT-2026-001002", "idem-key-2")
    n_id = params["id"]

    repo = PostgresNotificationRepository(db_session)
    notification = Notification.create(**params).value
    await repo.save(notification)
    await db_session.flush()

    # Transition to PROCESSING
    retrieved = await repo.get_by_id(n_id)
    retrieved.start_processing().value
    await repo.save(retrieved)
    await db_session.flush()

    # Verify PROCESSING is persisted
    retrieved = await repo.get_by_id(n_id)
    assert retrieved.status == NotificationStatus.PROCESSING
    assert retrieved.processing_started_at is not None

    # Transition to DELIVERED
    retrieved.mark_delivered().value
    await repo.save(retrieved)
    await db_session.flush()

    # Verify DELIVERED is persisted
    retrieved = await repo.get_by_id(n_id)
    assert retrieved.status == NotificationStatus.DELIVERED
    assert retrieved.delivered_at is not None


@pytest.mark.asyncio
async def test_notification_tenant_isolation(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())

    params_a = _create_base_params(org_a, NotificationChannel.EMAIL, "bob@a.com", "NOT-2026-001003", "idem-key-3")
    params_b = _create_base_params(org_b, NotificationChannel.EMAIL, "alice@b.com", "NOT-2026-001004", "idem-key-4")

    repo = PostgresNotificationRepository(db_session)
    notif_a = Notification.create(**params_a).value
    notif_b = Notification.create(**params_b).value
    await repo.save(notif_a)
    await repo.save(notif_b)
    await db_session.flush()

    # Verify Org A queries find only Org A's records
    results_a = await repo.find_by_organization(OrganizationId(org_a))
    assert len(results_a) == 1
    assert results_a[0].id == params_a["id"]

    results_b = await repo.find_by_organization(OrganizationId(org_b))
    assert len(results_b) == 1
    assert results_b[0].id == params_b["id"]


@pytest.mark.asyncio
async def test_notification_uniqueness_constraints(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())

    # Save base notification
    params_a = _create_base_params(org_a, NotificationChannel.EMAIL, "bob@a.com", "NOT-2026-001005", "idem-key-5")
    repo = PostgresNotificationRepository(db_session)
    notif = Notification.create(**params_a).value
    await repo.save(notif)
    await db_session.flush()

    # 1. Duplicate Reference must be rejected (regardless of Organization)
    params_dup_ref = _create_base_params(org_b, NotificationChannel.EMAIL, "alice@b.com", "NOT-2026-001005", "idem-key-6")
    notif_dup_ref = Notification.create(**params_dup_ref).value
    with pytest.raises(ValueError, match="DUPLICATE_NOTIFICATION_REFERENCE"):
        await repo.save(notif_dup_ref)
        await db_session.flush()
    await db_session.rollback()

    # Re-insert base notification since rollback cleared it
    notif = Notification.create(**params_a).value
    await repo.save(notif)
    await db_session.flush()

    # 2. Duplicate Idempotency Key within the SAME organization must be rejected
    params_dup_idem = _create_base_params(org_a, NotificationChannel.EMAIL, "bob@a.com", "NOT-2026-001006", "idem-key-5")
    notif_dup_idem = Notification.create(**params_dup_idem).value
    with pytest.raises(ValueError, match="DUPLICATE_NOTIFICATION_IDEMPOTENCY"):
        await repo.save(notif_dup_idem)
        await db_session.flush()
    await db_session.rollback()

    # Re-insert base notification since rollback cleared it
    notif = Notification.create(**params_a).value
    await repo.save(notif)
    await db_session.flush()

    # 3. Same Idempotency Key across DIFFERENT organizations succeeds
    params_diff_org_same_idem = _create_base_params(org_b, NotificationChannel.EMAIL, "alice@b.com", "NOT-2026-001007", "idem-key-5")
    notif_ok = Notification.create(**params_diff_org_same_idem).value
    await repo.save(notif_ok)
    await db_session.flush()


@pytest.mark.asyncio
async def test_notification_pessimistic_locking(db_session: AsyncSession):
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.EMAIL, "bob@example.com", "NOT-2026-001008", "idem-key-8")

    repo = PostgresNotificationRepository(db_session)
    notif = Notification.create(**params).value
    await repo.save(notif)
    await db_session.flush()

    # Retrieve with lock=True
    locked = await repo.get_by_id(params["id"], lock=True)
    assert locked is not None
    assert locked.id == params["id"]


@pytest.mark.asyncio
async def test_notification_outbox_transactional_atomicity(db_session: AsyncSession):
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.EMAIL, "bob@example.com", "NOT-2026-001009", "idem-key-9")

    # 1. Successful commit saves both notification and outbox event
    repo = PostgresNotificationRepository(db_session)
    notification = Notification.create(**params).value
    await repo.save(notification)
    await db_session.flush()

    # Verify notification is present
    retrieved = await repo.get_by_id(params["id"])
    assert retrieved is not None

    # Verify outbox event is present
    stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "NotificationCreated")
    events = (await db_session.execute(stmt)).scalars().all()
    assert any(str(params["id"].value) in e.payload for e in events)


@pytest.mark.asyncio
async def test_notification_audit_protection_rejects_delete(db_session: AsyncSession):
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.EMAIL, "bob@example.com", "NOT-2026-001010", "idem-key-10")

    repo = PostgresNotificationRepository(db_session)
    notif = Notification.create(**params).value
    await repo.save(notif)
    await db_session.flush()

    with pytest.raises(ValueError, match="Audit Trail Protection: Deleting a operational Notification record is rejected"):
        await repo.delete(params["id"])
