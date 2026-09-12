import pytest
from uuid import uuid4
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.src.infrastructure.database.models import OutboxEventDbModel
from nomadx.src.infrastructure.database.repositories import PostgresNotificationRepository
from nomadx.src.application.notification.commands import (
    CreateNotificationCommand,
    StartNotificationProcessingCommand,
    MarkNotificationDeliveredCommand,
    MarkNotificationFailedCommand,
    RetryNotificationCommand,
    CancelNotificationCommand
)
from nomadx.src.application.notification.handlers import (
    CreateNotificationHandler,
    StartNotificationProcessingHandler,
    MarkNotificationDeliveredHandler,
    MarkNotificationFailedHandler,
    RetryNotificationHandler,
    CancelNotificationHandler
)
from nomadx.src.domain.notification.notification import NotificationStatus
from nomadx.src.domain.notification.value_objects import NotificationId
from nomadx.src.presentation.api.dependencies.context import ExecutionContext


def _create_context(org_id: str) -> ExecutionContext:
    # Mimic ExecutionContext mapping authenticated organization ID
    return ExecutionContext(
        user_id=str(uuid4()),
        organization_id=org_id,
        role="ADMIN"
    )


def _create_base_params(org_id: str, reference: str, idempotency_key: str):
    return {
        "notification_id": str(uuid4()),
        "organization_id": org_id,
        "reference": reference,
        "notification_type": "INVOICE_ISSUED",
        "recipient_destination": "bob@example.com",
        "channel": "EMAIL",
        "subject": "Invoice issued successfully",
        "template_id": "invoice_issued",
        "payload": {"customer_name": "Bob", "amount": "50.00"},
        "priority": "NORMAL",
        "idempotency_key": idempotency_key,
        "source_event_id": str(uuid4()),
        "source_event_type": "InvoiceIssued"
    }


@pytest.mark.asyncio
async def test_notification_creation_success(db_session: AsyncSession):
    org_id = str(uuid4())
    ctx = _create_context(org_id)
    repo = PostgresNotificationRepository(db_session)
    handler = CreateNotificationHandler(repo)

    params = _create_base_params(org_id, "NOT-2026-000101", "idem-app-1")
    cmd = CreateNotificationCommand(**params)

    res = await handler.handle(cmd, ctx)
    assert res.is_success is True
    await db_session.flush()
    
    n = res.value
    assert n.reference.value == "NOT-2026-000101"
    assert n.status == NotificationStatus.PENDING

    # Check outbox
    stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "NotificationCreated")
    events = (await db_session.execute(stmt)).scalars().all()
    assert any(str(n.id.value) in e.payload for e in events)


@pytest.mark.asyncio
async def test_notification_creation_tenant_mismatch(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())
    ctx_b = _create_context(org_b) # Context for Org B
    repo = PostgresNotificationRepository(db_session)
    handler = CreateNotificationHandler(repo)

    # Command tries to create for Org A
    params = _create_base_params(org_a, "NOT-2026-000102", "idem-app-2")
    cmd = CreateNotificationCommand(**params)

    res = await handler.handle(cmd, ctx_b)
    assert res.is_failure is True
    assert res.error.code == "UNAUTHORIZED_ORGANIZATION_ACCESS"


@pytest.mark.asyncio
async def test_notification_creation_idempotency(db_session: AsyncSession):
    org_id = str(uuid4())
    ctx = _create_context(org_id)
    repo = PostgresNotificationRepository(db_session)
    handler = CreateNotificationHandler(repo)

    params = _create_base_params(org_id, "NOT-2026-000103", "idem-app-3")
    cmd = CreateNotificationCommand(**params)

    # First save
    res1 = await handler.handle(cmd, ctx)
    assert res1.is_success is True
    await db_session.flush()

    # Second save with exact same idempotency key
    res2 = await handler.handle(cmd, ctx)
    assert res2.is_success is True
    assert res2.value.id == res1.value.id # Returns existing instance


@pytest.mark.asyncio
async def test_notification_creation_source_event_deduplication(db_session: AsyncSession):
    org_id = str(uuid4())
    ctx = _create_context(org_id)
    repo = PostgresNotificationRepository(db_session)
    handler = CreateNotificationHandler(repo)

    params = _create_base_params(org_id, "NOT-2026-000104", "idem-app-4")
    # Same source event id and type
    src_id = str(uuid4())
    params["source_event_id"] = src_id
    params["source_event_type"] = "InvoiceIssued"

    cmd1 = CreateNotificationCommand(**params)
    res1 = await handler.handle(cmd1, ctx)
    assert res1.is_success is True
    await db_session.flush()

    # Create new command with different idempotency key and reference but same source event
    params["reference"] = "NOT-2026-000105"
    params["idempotency_key"] = "idem-app-5"
    cmd2 = CreateNotificationCommand(**params)

    res2 = await handler.handle(cmd2, ctx)
    assert res2.is_success is True
    assert res2.value.id == res1.value.id # Returns existing instance


@pytest.mark.asyncio
async def test_notification_lifecycle_transitions(db_session: AsyncSession):
    org_id = str(uuid4())
    ctx = _create_context(org_id)
    repo = PostgresNotificationRepository(db_session)
    
    create_h = CreateNotificationHandler(repo)
    start_h = StartNotificationProcessingHandler(repo)
    deliver_h = MarkNotificationDeliveredHandler(repo)
    fail_h = MarkNotificationFailedHandler(repo)
    retry_h = RetryNotificationHandler(repo)
    cancel_h = CancelNotificationHandler(repo)

    # 1. Create PENDING
    params = _create_base_params(org_id, "NOT-2026-000106", "idem-app-6")
    n = (await create_h.handle(CreateNotificationCommand(**params), ctx)).value
    await db_session.flush()

    # 2. PENDING -> PROCESSING
    n = (await start_h.handle(StartNotificationProcessingCommand(n.id.value, org_id), ctx)).value
    assert n.status == NotificationStatus.PROCESSING
    await db_session.flush()

    # 3. PROCESSING -> DELIVERED
    n = (await deliver_h.handle(MarkNotificationDeliveredCommand(n.id.value, org_id), ctx)).value
    assert n.status == NotificationStatus.DELIVERED
    await db_session.flush()

    # 4. Create another notification to test failure and retries
    params2 = _create_base_params(org_id, "NOT-2026-000107", "idem-app-7")
    n2 = (await create_h.handle(CreateNotificationCommand(**params2), ctx)).value
    await db_session.flush()

    # Move to PROCESSING then FAILED (retryable)
    await start_h.handle(StartNotificationProcessingCommand(n2.id.value, org_id), ctx)
    await db_session.flush()

    n2 = (await fail_h.handle(MarkNotificationFailedCommand(
        n2.id.value, org_id, "EMAIL_PROVIDER_TIMEOUT", "Timeout occurred", True
    ), ctx)).value
    assert n2.status == NotificationStatus.FAILED
    assert n2.failure.retryable is True
    await db_session.flush()

    # FAILED -> RETRYING
    n2 = (await retry_h.handle(RetryNotificationCommand(n2.id.value, org_id), ctx)).value
    assert n2.status == NotificationStatus.RETRYING
    await db_session.flush()

    # Create third to test cancellation
    params3 = _create_base_params(org_id, "NOT-2026-000108", "idem-app-8")
    n3 = (await create_h.handle(CreateNotificationCommand(**params3), ctx)).value
    await db_session.flush()

    # PENDING -> CANCELLED
    n3 = (await cancel_h.handle(CancelNotificationCommand(n3.id.value, org_id), ctx)).value
    assert n3.status == NotificationStatus.CANCELLED


@pytest.mark.asyncio
async def test_notification_failure_rules(db_session: AsyncSession):
    org_id = str(uuid4())
    ctx = _create_context(org_id)
    repo = PostgresNotificationRepository(db_session)
    
    create_h = CreateNotificationHandler(repo)
    start_h = StartNotificationProcessingHandler(repo)
    fail_h = MarkNotificationFailedHandler(repo)
    retry_h = RetryNotificationHandler(repo)

    # Permanent failure test
    params = _create_base_params(org_id, "NOT-2026-000109", "idem-app-9")
    n = (await create_h.handle(CreateNotificationCommand(**params), ctx)).value
    await db_session.flush()

    await start_h.handle(StartNotificationProcessingCommand(n.id.value, org_id), ctx)
    await db_session.flush()

    # Save permanent failure
    res_fail = await fail_h.handle(MarkNotificationFailedCommand(
        n.id.value, org_id, "INVALID_RECIPIENT", "Invalid recipient format", False
    ), ctx)
    assert res_fail.is_success is True
    assert res_fail.value.status == NotificationStatus.FAILED
    await db_session.flush()

    # Attempt retry on permanent failure must be rejected
    res_retry = await retry_h.handle(RetryNotificationCommand(n.id.value, org_id), ctx)
    assert res_retry.is_failure is True
    assert res_retry.error.code == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_notification_security_guard(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())

    ctx_a = _create_context(org_a)
    ctx_b = _create_context(org_b) # Org B attempts on Org A's notification

    repo = PostgresNotificationRepository(db_session)
    create_h = CreateNotificationHandler(repo)
    start_h = StartNotificationProcessingHandler(repo)

    params = _create_base_params(org_a, "NOT-2026-000110", "idem-app-10")
    n = (await create_h.handle(CreateNotificationCommand(**params), ctx_a)).value
    await db_session.flush()

    # Org B attempts to transition Org A's notification
    res = await start_h.handle(StartNotificationProcessingCommand(n.id.value, org_a), ctx_b)
    assert res.is_failure is True
    assert res.error.code == "UNAUTHORIZED_ORGANIZATION_ACCESS"
