import pytest
from uuid import uuid4
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.notification.value_objects import (
    NotificationId,
    NotificationReference,
    NotificationRecipient,
    NotificationChannel,
    NotificationType,
    NotificationPriority,
    NotificationTemplateId,
    NotificationPayload,
    NotificationIdempotencyKey,
    NotificationFailure,
    NotificationFailureCode
)
from nomadx.src.domain.notification.notification import Notification, NotificationStatus
from nomadx.src.infrastructure.database.models import (
    NotificationDbModel,
    NotificationDeliveryAttemptDbModel,
    ProviderStateDbModel,
    OrganizationDbModel,
    UserDbModel
)
from nomadx.src.infrastructure.workers.notification_worker import NotificationWorker, BackoffPolicy
from nomadx.src.infrastructure.notification.circuit_breaker import ProviderCircuitBreaker
from nomadx.src.infrastructure.notification.models import NotificationDeliveryResult
from nomadx.src.application.notification.replay_commands import ReplayNotificationCommand
from nomadx.src.application.notification.replay_handlers import ReplayNotificationHandler
from nomadx.src.infrastructure.database.repositories import PostgresNotificationRepository
from nomadx.src.presentation.api.dependencies.context import ExecutionContext


class FakeDeliveryAdapter:
    def __init__(self, should_succeed: bool = True, failure_retryable: bool = True):
        self.should_succeed = should_succeed
        self.failure_retryable = failure_retryable
        self.deliveries = []

    async def deliver(self, request):
        self.deliveries.append(request)
        if self.should_succeed:
            return NotificationDeliveryResult(
                is_success=True,
                provider_reference="prov-ref-123"
            )
        else:
            return NotificationDeliveryResult(
                is_success=False,
                failure_code="PROVIDER_ERROR",
                failure_message="Connection timed out.",
                failure_retryable=self.failure_retryable
            )


async def _create_test_org(session: AsyncSession, org_id: str):
    owner = UserDbModel(
        id=str(uuid4()),
        email="merchant@example.com",
        password_hash="hash",
        name="Merchant Admin",
        status="ACTIVE"
    )
    org = OrganizationDbModel(
        id=org_id,
        name="NomadX Corp",
        slug=f"nomadx-corp-{org_id[:8]}",
        status="ACTIVE",
        default_currency="USD",
        owner_id=owner.id
    )
    session.add(owner)
    session.add(org)
    await session.flush()


@pytest.mark.asyncio
async def test_retry_exhaustion_transitions_to_dead_lettered(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    # 1. Create a notification in pending state with max_attempts = 2
    repo = PostgresNotificationRepository(db_session)
    notif = Notification.create(
        id=NotificationId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        reference=NotificationReference("NOT-TEST-1"),
        notification_type=NotificationType.INVOICE_ISSUED,
        recipient=NotificationRecipient(NotificationChannel.EMAIL, "alice@test.com"),
        channel=NotificationChannel.EMAIL,
        subject="Invoice Notification",
        template_id=NotificationTemplateId("template.1"),
        payload=NotificationPayload({"body": "Invoice issued"}),
        priority=NotificationPriority.NORMAL,
        idempotency_key=NotificationIdempotencyKey("idem-test-1"),
        source_event_id=str(uuid4()),
        source_event_type="InvoiceIssued",
        max_attempts=2
    ).value
    await repo.save(notif)
    await db_session.commit()

    # Configure adapter to always fail with a retryable error
    adapter = FakeDeliveryAdapter(should_succeed=False, failure_retryable=True)
    worker = NotificationWorker(adapter)

    # Run Loop 1 (Attempts delivery -> fails -> transitions to FAILED and schedules retry)
    processed = await worker.process_pending_notifications()
    assert processed == 1

    db_session.expire_all()
    n1 = await repo.get_by_id(notif.id)
    assert n1.status == NotificationStatus.RETRYING
    assert n1.retry_count == 1

    # Shift clock to bypass backoff delay
    # Re-schedule to execute immediately
    async with db_session.begin_nested():
        stmt = select(NotificationDbModel).where(NotificationDbModel.id == notif.id.value)
        db_model = (await db_session.execute(stmt)).scalar_one()
        db_model.next_retry_at = Clock.now() - timedelta(seconds=1)
    await db_session.commit()

    # Run Loop 2 (Attempts delivery again -> fails -> exceeds max_attempts (2) -> transitions to DEAD_LETTERED)
    processed_2 = await worker.process_pending_notifications()
    assert processed_2 == 1

    db_session.expire_all()
    n2 = await repo.get_by_id(notif.id)
    assert n2.status == NotificationStatus.DEAD_LETTERED
    assert n2.retry_count == 2
    assert "Connection timed out." in n2.failure.message


@pytest.mark.asyncio
async def test_replay_handler_resets_delivery_controls(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    # Save a dead-lettered notification in the database
    repo = PostgresNotificationRepository(db_session)
    notif = Notification.create(
        id=NotificationId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        reference=NotificationReference("NOT-TEST-REPLAY"),
        notification_type=NotificationType.INVOICE_ISSUED,
        recipient=NotificationRecipient(NotificationChannel.EMAIL, "alice@test.com"),
        channel=NotificationChannel.EMAIL,
        subject="Invoice Notification",
        template_id=NotificationTemplateId("template.1"),
        payload=NotificationPayload({"body": "Invoice issued"}),
        priority=NotificationPriority.NORMAL,
        idempotency_key=NotificationIdempotencyKey("idem-replay-1"),
        source_event_id=str(uuid4()),
        source_event_type="InvoiceIssued"
    ).value
    # Transition manually to DEAD_LETTERED
    notif.start_processing()
    notif.mark_failed(NotificationFailure(NotificationFailureCode.UNKNOWN_ERROR, "exhausted", False))
    notif.mark_dead_lettered("Manual dead letter")
    await repo.save(notif)
    await db_session.commit()

    handler = ReplayNotificationHandler(repo)

    # 1. Reject if MEMBER role
    ctx_member = ExecutionContext(user_id="user_1", organization_id=org_id, role="MEMBER")
    cmd = ReplayNotificationCommand(notification_id=notif.id.value, organization_id=org_id)
    res_member = await handler.handle(cmd, ctx_member)
    assert res_member.is_failure
    assert "Action requires ADMIN or OWNER" in res_member.error.message

    # 2. Allow if ADMIN role and correct tenant
    ctx_admin = ExecutionContext(user_id="user_1", organization_id=org_id, role="ADMIN")
    res_admin = await handler.handle(cmd, ctx_admin)
    assert res_admin.is_success
    
    # Reload and verify reset fields
    db_session.expire_all()
    n_replayed = await repo.get_by_id(notif.id)
    assert n_replayed.status == NotificationStatus.PENDING
    assert n_replayed.retry_count == 0
    assert n_replayed.replay_count == 1
    assert n_replayed.failure is None


@pytest.mark.asyncio
async def test_provider_circuit_breaker_transitions_and_cooldown(db_session: AsyncSession):
    provider_name = "FakeDeliveryAdapter"
    cb = ProviderCircuitBreaker(db_session, failure_threshold=2, cooldown_seconds=2)

    # Check initially closed/available
    assert await cb.check_availability(provider_name) is True

    # Record 1 failure
    await cb.record_failure(provider_name)
    assert await cb.check_availability(provider_name) is True

    # Record 2 failures -> exceeds threshold (2) -> transitions to OPEN
    await cb.record_failure(provider_name)
    assert await cb.check_availability(provider_name) is False

    # Wait for cooldown period (2 seconds)
    import asyncio
    await asyncio.sleep(2.1)

    # Checking availability triggers HALF_OPEN probe transition and returns True
    assert await cb.check_availability(provider_name) is True
    
    stmt = select(ProviderStateDbModel).where(ProviderStateDbModel.provider_name == provider_name)
    state = (await db_session.execute(stmt)).scalar_one()
    assert state.circuit_state == "HALF_OPEN"

    # Record success -> resets to CLOSED
    await cb.record_success(provider_name)
    db_session.expire_all()
    
    stmt = select(ProviderStateDbModel).where(ProviderStateDbModel.provider_name == provider_name)
    state = (await db_session.execute(stmt)).scalar_one()
    assert state.circuit_state == "CLOSED"
    assert state.consecutive_failures == 0


@pytest.mark.asyncio
async def test_worker_rate_limiting_throttles_execution(db_session: AsyncSession):
    org_id = str(uuid4())
    await _create_test_org(db_session, org_id)

    # Create 2 notifications
    repo = PostgresNotificationRepository(db_session)
    n1 = Notification.create(
        id=NotificationId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        reference=NotificationReference("NOT-RATE-1"),
        notification_type=NotificationType.INVOICE_ISSUED,
        recipient=NotificationRecipient(NotificationChannel.EMAIL, "alice@test.com"),
        channel=NotificationChannel.EMAIL,
        subject="Rate 1",
        template_id=NotificationTemplateId("template.1"),
        payload=NotificationPayload({"body": "rate 1"}),
        priority=NotificationPriority.NORMAL,
        idempotency_key=NotificationIdempotencyKey("idem-rate-1"),
        source_event_id=str(uuid4()),
        source_event_type="InvoiceIssued"
    ).value

    n2 = Notification.create(
        id=NotificationId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        reference=NotificationReference("NOT-RATE-2"),
        notification_type=NotificationType.INVOICE_ISSUED,
        recipient=NotificationRecipient(NotificationChannel.EMAIL, "bob@test.com"),
        channel=NotificationChannel.EMAIL,
        subject="Rate 2",
        template_id=NotificationTemplateId("template.1"),
        payload=NotificationPayload({"body": "rate 2"}),
        priority=NotificationPriority.NORMAL,
        idempotency_key=NotificationIdempotencyKey("idem-rate-2"),
        source_event_id=str(uuid4()),
        source_event_type="InvoiceIssued"
    ).value

    await repo.save(n1)
    await repo.save(n2)
    await db_session.commit()

    # Configure worker rate limit for EMAIL to be 1 per minute
    adapter = FakeDeliveryAdapter(should_succeed=True)
    worker = NotificationWorker(adapter, rate_limits={"EMAIL": 1})

    # Run Loop -> should process exactly 1 notification, and skip the second one because of rate limit!
    processed = await worker.process_pending_notifications()
    assert processed == 1

    db_session.expire_all()
    # Check that 1 notification is DELIVERED, and the other remains PENDING
    stmt = select(NotificationDbModel.status)
    statuses = (await db_session.execute(stmt)).scalars().all()
    assert "DELIVERED" in statuses
    assert "PENDING" in statuses
