import pytest
import asyncio
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.notification.template import (
    NotificationTemplate,
    TemplateId,
    TemplateReference,
    TemplateVersion,
    TemplateContent,
    TemplateStatus
)
from nomadx.src.infrastructure.database.repositories import (
    PostgresNotificationRepository,
    PostgresNotificationTemplateRepository
)
from nomadx.src.infrastructure.database.query_service import PostgresNotificationQueryService
from nomadx.src.infrastructure.database.models import (
    NotificationDbModel,
    NotificationTemplateDbModel,
    NotificationDeliveryAttemptDbModel
)
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
    NotificationIdempotencyKey
)
from nomadx.src.infrastructure.notification.fake import (
    FakeEmailDeliveryAdapter,
    FakeWebhookDeliveryAdapter,
    NotificationDeliveryAdapter
)
from nomadx.src.infrastructure.workers.notification_worker import NotificationWorker


def _create_base_params(org_id: str, channel: NotificationChannel, destination: str, reference: str, idempotency_key: str, scheduled_at=None):
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
        "source_event_type": "InvoiceIssued",
        "scheduled_at": scheduled_at
    }


@pytest.mark.asyncio
async def test_template_version_uniqueness_and_active_constraint(db_session: AsyncSession):
    org_id = OrganizationId(str(uuid4()))
    repo = PostgresNotificationTemplateRepository(db_session)

    # 1. Save v1 template
    t1 = NotificationTemplate.create(
        id=TemplateId(str(uuid4())),
        organization_id=org_id,
        reference=TemplateReference("invoice.created"),
        version=TemplateVersion("v1"),
        content=TemplateContent("First version"),
        status=TemplateStatus.ACTIVE
    ).value
    await repo.save(t1)
    await db_session.commit()

    # 2. Try to save another v1 template - must raise error due to DB unique constraints
    t1_dup = NotificationTemplate.create(
        id=TemplateId(str(uuid4())),
        organization_id=org_id,
        reference=TemplateReference("invoice.created"),
        version=TemplateVersion("v1"),
        content=TemplateContent("Duplicate version"),
        status=TemplateStatus.INACTIVE
    ).value
    with pytest.raises(ValueError, match="DUPLICATE_TEMPLATE_VERSION"):
        await repo.save(t1_dup)
        await db_session.commit()
    await db_session.rollback()

    # 3. Try to save another ACTIVE version (v2) - active index constraint should block it
    t2_active = NotificationTemplate.create(
        id=TemplateId(str(uuid4())),
        organization_id=org_id,
        reference=TemplateReference("invoice.created"),
        version=TemplateVersion("v2"),
        content=TemplateContent("Second active version"),
        status=TemplateStatus.ACTIVE
    ).value
    with pytest.raises(ValueError, match="DUPLICATE_ACTIVE_TEMPLATE"):
        await repo.save(t2_active)
        await db_session.commit()
    await db_session.rollback()

    # 4. Deleting template aggregate must raise operational audit trail block
    with pytest.raises(ValueError, match="Audit Trail Protection"):
        await repo.delete(t1.id)


@pytest.mark.asyncio
async def test_worker_respects_scheduling(db_session: AsyncSession):
    org_id = str(uuid4())
    now = Clock.now()

    # Future scheduled notification: should be skipped
    future_time = now + timedelta(hours=1)
    params_future = _create_base_params(org_id, NotificationChannel.EMAIL, "alice@example.com", "NOT-SCHED-001", "idem-sched-1", scheduled_at=future_time)
    
    # Past/Now scheduled notification: should be processed
    params_now = _create_base_params(org_id, NotificationChannel.EMAIL, "bob@example.com", "NOT-SCHED-002", "idem-sched-2", scheduled_at=now)

    repo = PostgresNotificationRepository(db_session)
    n_future = Notification.create(**params_future).value
    n_now = Notification.create(**params_now).value

    await repo.save(n_future)
    await repo.save(n_now)
    await db_session.commit()

    email_adapter = FakeEmailDeliveryAdapter()
    webhook_adapter = FakeWebhookDeliveryAdapter()
    composite_adapter = NotificationDeliveryAdapter(email_adapter, webhook_adapter)
    worker = NotificationWorker(composite_adapter)

    # Run worker
    processed = await worker.process_pending_notifications()
    assert processed == 1

    # Expire to clear cached session identity map
    db_session.expire_all()

    # Verify n_now is DELIVERED, n_future is still PENDING
    ret_now = await repo.get_by_id(n_now.id)
    ret_future = await repo.get_by_id(n_future.id)

    assert ret_now.status == NotificationStatus.DELIVERED
    assert ret_future.status == NotificationStatus.PENDING


@pytest.mark.asyncio
async def test_worker_retries_and_max_attempts(db_session: AsyncSession):
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.EMAIL, "fail@example.com", "NOT-FAIL-001", "idem-fail-1")
    n_id = params["id"]

    repo = PostgresNotificationRepository(db_session)
    notification = Notification.create(**params).value
    await repo.save(notification)
    await db_session.commit()

    # Create worker with adapter that returns temporary/retryable failure
    email_adapter = FakeEmailDeliveryAdapter()
    email_adapter.set_behavior(n_id.value, "TEMPORARY_FAILURE")
    
    webhook_adapter = FakeWebhookDeliveryAdapter()
    composite_adapter = NotificationDeliveryAdapter(email_adapter, webhook_adapter)
    worker = NotificationWorker(composite_adapter)

    # Execution 1
    processed = await worker.process_pending_notifications()
    assert processed == 1

    # Reload: status should be RETRYING, retry_count=1, next_retry_at set
    db_session.expire_all()
    retrieved = await repo.get_by_id(n_id)
    assert retrieved.status == NotificationStatus.RETRYING
    assert retrieved.retry_count == 1
    assert retrieved.next_retry_at is not None

    # Artificially shift next_retry_at to past to process again
    async with db_session.begin_nested():
        stmt = select(NotificationDbModel).where(NotificationDbModel.id == n_id.value)
        db_model = (await db_session.execute(stmt)).scalar_one()
        db_model.next_retry_at = Clock.now() - timedelta(seconds=1)
    await db_session.commit()

    # Execution 2
    processed = await worker.process_pending_notifications()
    assert processed == 1

    db_session.expire_all()
    retrieved = await repo.get_by_id(n_id)
    assert retrieved.status == NotificationStatus.RETRYING
    assert retrieved.retry_count == 2

    # Artificially shift next_retry_at to past again
    async with db_session.begin_nested():
        stmt = select(NotificationDbModel).where(NotificationDbModel.id == n_id.value)
        db_model = (await db_session.execute(stmt)).scalar_one()
        db_model.next_retry_at = Clock.now() - timedelta(seconds=1)
    await db_session.commit()

    # Execution 3: reaches max_attempts (3) -> should transition to FAILED permanently
    processed = await worker.process_pending_notifications()
    assert processed == 1

    db_session.expire_all()
    retrieved = await repo.get_by_id(n_id)
    assert retrieved.status == NotificationStatus.DEAD_LETTERED
    assert retrieved.retry_count == 3


@pytest.mark.asyncio
async def test_read_side_reporting_aggregations(db_session: AsyncSession):
    org_id = OrganizationId(str(uuid4()))
    repo = PostgresNotificationRepository(db_session)
    query_service = PostgresNotificationQueryService(db_session)

    # Create 3 notifications: 1 delivered, 1 failed, 1 pending
    n1 = Notification.create(**_create_base_params(org_id.value, NotificationChannel.EMAIL, "a@example.com", "NOT-REP-01", "idem-rep-1")).value
    n2 = Notification.create(**_create_base_params(org_id.value, NotificationChannel.WEBHOOK, "https://webhook.org/1", "NOT-REP-02", "idem-rep-2")).value
    n3 = Notification.create(**_create_base_params(org_id.value, NotificationChannel.EMAIL, "b@example.com", "NOT-REP-03", "idem-rep-3")).value

    # Manually transition n1 to DELIVERED, n2 to FAILED
    n1.start_processing()
    n1.mark_delivered()

    n2.start_processing()
    from nomadx.src.domain.notification.value_objects import NotificationFailure, NotificationFailureCode
    n2.mark_failed(NotificationFailure(NotificationFailureCode.PROVIDER_REJECTED, "Rejected", False))

    await repo.save(n1)
    await repo.save(n2)
    await repo.save(n3)
    await db_session.commit()

    # Log 2 custom attempts for n2
    a1 = NotificationDeliveryAttemptDbModel(
        id=str(uuid4()),
        notification_id=n2.id.value,
        organization_id=org_id.value,
        attempt_number=1,
        channel=NotificationChannel.WEBHOOK.value,
        provider="FakeProvider",
        started_at=Clock.now(),
        completed_at=Clock.now(),
        is_success=False,
        failure_code="PROVIDER_REJECTED",
        failure_message="Rejected"
    )
    db_session.add(a1)
    await db_session.commit()

    db_session.expire_all()

    # 1. Verify get_notification_summary
    summary = await query_service.get_notification_summary(org_id)
    assert summary["total"] == 3
    assert summary["delivered"] == 1
    assert summary["failed"] == 1
    assert summary["pending"] == 1

    # 2. Verify get_delivery_stats
    stats = await query_service.get_delivery_stats(org_id)
    assert stats["EMAIL"]["delivered"] == 1
    assert stats["WEBHOOK"]["failed"] == 1

    # 3. Verify get_delivery_attempts
    attempts = await query_service.get_delivery_attempts(org_id, n2.id.value)
    assert len(attempts) == 1
    assert attempts[0]["attempt_number"] == 1
    assert attempts[0]["failure_code"] == "PROVIDER_REJECTED"
