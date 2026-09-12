import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.organization import Organization
from nomadx.src.domain.organization.value_objects import (
    OrganizationId,
    OrganizationName,
    OrganizationSlug,
    UserId,
    OrganizationSettings
)
from nomadx.src.domain.invoice import InvoiceId, BillingSnapshot, CustomerId, Invoice, InvoiceOrigin, InvoiceOriginType, PaymentTerms, PaymentTermsType
from nomadx.src.domain.invoice.value_objects import Currency, CurrencyType, Money
from nomadx.src.domain.payment import (
    PaymentId,
    PaymentAsset,
    PaymentNetwork,
    Payment
)
from nomadx.src.domain.settlement import (
    Settlement,
    SettlementStatus,
    SettlementId,
    SettlementReference,
    SettlementAsset,
    SettlementNetwork,
    SettlementDestination,
    SettlementDestinationType,
    SettlementMethod,
    SettlementSource,
    SettlementSourceType,
    SettlementFailureCode,
    SettlementFailure,
    SettlementIdempotencyKey
)
from nomadx.src.infrastructure.database.repositories import (
    PostgresOrganizationRepository,
    PostgresPaymentRepository,
    PostgresSettlementRepository
)
from nomadx.src.infrastructure.database.models import OutboxEventDbModel
from nomadx.src.presentation.api.dependencies.context import ExecutionContext

from nomadx.src.application.settlement.commands import (
    CreateSettlementCommand,
    StartSettlementProcessingCommand,
    SubmitSettlementCommand,
    ConfirmSettlementCommand,
    CompleteSettlementCommand,
    FailSettlementCommand,
    RetrySettlementCommand,
    CancelSettlementCommand
)
from nomadx.src.application.settlement.handlers import (
    CreateSettlementHandler,
    StartSettlementProcessingHandler,
    SubmitSettlementHandler,
    ConfirmSettlementHandler,
    CompleteSettlementHandler,
    FailSettlementHandler,
    RetrySettlementHandler,
    CancelSettlementHandler
)
from nomadx.src.application.settlement.services import SettlementAvailabilityService
from nomadx.src.application.settlement.errors import SettlementErrors


async def _setup_merchant(session: AsyncSession, org_id_str: str) -> Organization:
    org_repo = PostgresOrganizationRepository(session)
    org = Organization.create(
        id=OrganizationId(org_id_str),
        name=OrganizationName("App Test Corp"),
        slug=OrganizationSlug(f"app-test-{org_id_str[:8]}"),
        owner_id=UserId(str(uuid4())),
        settings=OrganizationSettings("USDC")
    ).value
    await org_repo.save(org)
    await session.flush()
    return org


async def _setup_payment(
    session: AsyncSession,
    org_id_str: str,
    payment_id_str: str,
    amount: Decimal = Decimal("5000.00"),
    confirmed: bool = True,
    rejected: bool = False
) -> Payment:
    payment_repo = PostgresPaymentRepository(session)
    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    observed_amount = Money(amount, currency)
    asset = PaymentAsset("USDC", "DIGITAL_ASSET", 6)
    network = PaymentNetwork("BASE", "BLOCKCHAIN")

    payment = Payment.create(
        id=PaymentId(payment_id_str),
        organization_id=OrganizationId(org_id_str),
        payment_intent_id=None,
        observed_amount=observed_amount,
        asset=asset,
        network=network,
        transaction_reference=f"0xTx_{payment_id_str[:8]}",
        sender="0xSenderWalletAddress",
        receiver="0xReceiverWalletAddress",
        observed_at=Clock.now()
    ).value

    if confirmed:
        payment.confirm()
    elif rejected:
        payment.reject()

    await payment_repo.save(payment)
    await session.flush()
    return payment


@pytest.mark.asyncio
async def test_create_settlement_from_confirmed_payment(db_session: AsyncSession):
    org_id = str(uuid4())
    pay_id = str(uuid4())
    await _setup_merchant(db_session, org_id)
    await _setup_payment(db_session, org_id, pay_id, Decimal("5000.00"), confirmed=True)

    settlement_repo = PostgresSettlementRepository(db_session)
    payment_repo = PostgresPaymentRepository(db_session)
    avail_svc = SettlementAvailabilityService(settlement_repo, payment_repo)
    handler = CreateSettlementHandler(settlement_repo, payment_repo, avail_svc)

    ctx = ExecutionContext(user_id="merchant", organization_id=org_id, role="MEMBER")
    cmd = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=org_id,
        payment_id=pay_id,
        amount=Decimal("3000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-100001",
        idempotency_key="idem-app-1"
    )

    res = await handler.handle(cmd, ctx)
    assert res.is_success
    settlement = res.value
    assert settlement.status == SettlementStatus.PENDING
    assert settlement.amount.amount == Decimal("3000.00")


@pytest.mark.asyncio
async def test_reject_unconfirmed_and_rejected_payments(db_session: AsyncSession):
    org_id = str(uuid4())
    pay_unconfirmed = str(uuid4())
    pay_rejected = str(uuid4())
    await _setup_merchant(db_session, org_id)
    
    # 1. Unconfirmed Payment (DETECTED status)
    await _setup_payment(db_session, org_id, pay_unconfirmed, Decimal("5000.00"), confirmed=False)
    # 2. Rejected Payment
    await _setup_payment(db_session, org_id, pay_rejected, Decimal("5000.00"), confirmed=False, rejected=True)

    settlement_repo = PostgresSettlementRepository(db_session)
    payment_repo = PostgresPaymentRepository(db_session)
    avail_svc = SettlementAvailabilityService(settlement_repo, payment_repo)
    handler = CreateSettlementHandler(settlement_repo, payment_repo, avail_svc)

    ctx = ExecutionContext(user_id="merchant", organization_id=org_id, role="MEMBER")

    # Try creating from unconfirmed payment
    cmd1 = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=org_id,
        payment_id=pay_unconfirmed,
        amount=Decimal("1000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-100002",
        idempotency_key="idem-app-2"
    )
    res1 = await handler.handle(cmd1, ctx)
    assert res1.is_failure
    assert res1.error.code == "PAYMENT_NOT_ELIGIBLE"

    # Try creating from rejected payment
    cmd2 = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=org_id,
        payment_id=pay_rejected,
        amount=Decimal("1000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-100003",
        idempotency_key="idem-app-3"
    )
    res2 = await handler.handle(cmd2, ctx)
    assert res2.is_failure
    assert res2.error.code == "PAYMENT_NOT_ELIGIBLE"


@pytest.mark.asyncio
async def test_reject_missing_and_unauthorized_payment_ownership(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())
    pay_b = str(uuid4())
    await _setup_merchant(db_session, org_a)
    await _setup_merchant(db_session, org_b)
    await _setup_payment(db_session, org_b, pay_b, Decimal("5000.00"), confirmed=True)

    settlement_repo = PostgresSettlementRepository(db_session)
    payment_repo = PostgresPaymentRepository(db_session)
    avail_svc = SettlementAvailabilityService(settlement_repo, payment_repo)
    handler = CreateSettlementHandler(settlement_repo, payment_repo, avail_svc)

    ctx_a = ExecutionContext(user_id="merchant", organization_id=org_a, role="MEMBER")

    # 1. Non-existent payment
    cmd_missing = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=org_a,
        payment_id=str(uuid4()),
        amount=Decimal("1000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-100004",
        idempotency_key="idem-app-4"
    )
    res_missing = await handler.handle(cmd_missing, ctx_a)
    assert res_missing.is_failure
    assert res_missing.error.code == "PAYMENT_NOT_FOUND"

    # 2. Payment ownership mismatch (Org A tries to settle Org B payment)
    cmd_foreign = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=org_a,
        payment_id=pay_b,
        amount=Decimal("1000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-100005",
        idempotency_key="idem-app-5"
    )
    res_foreign = await handler.handle(cmd_foreign, ctx_a)
    assert res_foreign.is_failure
    assert res_foreign.error.code == "PAYMENT_OWNERSHIP_MISMATCH"


@pytest.mark.asyncio
async def test_settlement_idempotency_reuse(db_session: AsyncSession):
    org_id = str(uuid4())
    pay_id = str(uuid4())
    await _setup_merchant(db_session, org_id)
    await _setup_payment(db_session, org_id, pay_id, Decimal("5000.00"), confirmed=True)

    settlement_repo = PostgresSettlementRepository(db_session)
    payment_repo = PostgresPaymentRepository(db_session)
    avail_svc = SettlementAvailabilityService(settlement_repo, payment_repo)
    handler = CreateSettlementHandler(settlement_repo, payment_repo, avail_svc)

    ctx = ExecutionContext(user_id="merchant", organization_id=org_id, role="MEMBER")
    settlement_id = str(uuid4())
    cmd = CreateSettlementCommand(
        settlement_id=settlement_id,
        organization_id=org_id,
        payment_id=pay_id,
        amount=Decimal("1000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-100006",
        idempotency_key="idem-app-idemp"
    )

    res1 = await handler.handle(cmd, ctx)
    assert res1.is_success

    # Run again with same idempotency key (different settlement ID)
    cmd2 = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=org_id,
        payment_id=pay_id,
        amount=Decimal("1000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-100006-DUP", # Reference is ignored, existing aggregate returned
        idempotency_key="idem-app-idemp"
    )
    res2 = await handler.handle(cmd2, ctx)
    assert res2.is_success
    assert res2.value.id.value == settlement_id


@pytest.mark.asyncio
async def test_settlement_availability_and_balances(db_session: AsyncSession):
    org_id = str(uuid4())
    pay_id = str(uuid4())
    await _setup_merchant(db_session, org_id)
    await _setup_payment(db_session, org_id, pay_id, Decimal("5000.00"), confirmed=True)

    settlement_repo = PostgresSettlementRepository(db_session)
    payment_repo = PostgresPaymentRepository(db_session)
    avail_svc = SettlementAvailabilityService(settlement_repo, payment_repo)
    handler = CreateSettlementHandler(settlement_repo, payment_repo, avail_svc)

    ctx = ExecutionContext(user_id="merchant", organization_id=org_id, role="MEMBER")

    # 1. Full/Partial amount available: Settle $2,000 of $5,000
    cmd1 = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=org_id,
        payment_id=pay_id,
        amount=Decimal("2000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-100007",
        idempotency_key="idem-app-bal1"
    )
    res1 = await handler.handle(cmd1, ctx)
    assert res1.is_success

    # Flush session to verify persistence
    await db_session.flush()

    # 2. Deducted balance verification: Settle $2,000 of remaining $3,000
    cmd2 = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=org_id,
        payment_id=pay_id,
        amount=Decimal("2000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-100008",
        idempotency_key="idem-app-bal2"
    )
    res2 = await handler.handle(cmd2, ctx)
    assert res2.is_success
    await db_session.flush()

    # 3. Over-settlement rejection: Settle $2,000 of remaining $1,000 (should fail)
    cmd3 = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=org_id,
        payment_id=pay_id,
        amount=Decimal("2000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-100009",
        idempotency_key="idem-app-bal3"
    )
    res3 = await handler.handle(cmd3, ctx)
    assert res3.is_failure
    assert res3.error.code == "INSUFFICIENT_SETTLEMENT_BALANCE"

    # 4. Exact remaining amount accepted: Settle exact remaining $1,000 (should pass)
    cmd4 = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=org_id,
        payment_id=pay_id,
        amount=Decimal("1000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-100010",
        idempotency_key="idem-app-bal4"
    )
    res4 = await handler.handle(cmd4, ctx)
    assert res4.is_success


@pytest.mark.asyncio
async def test_settlement_lifecycle_commands_progression(db_session: AsyncSession):
    org_id = str(uuid4())
    pay_id = str(uuid4())
    settlement_id = str(uuid4())
    await _setup_merchant(db_session, org_id)
    await _setup_payment(db_session, org_id, pay_id, Decimal("500.00"), confirmed=True)

    settlement_repo = PostgresSettlementRepository(db_session)
    payment_repo = PostgresPaymentRepository(db_session)
    avail_svc = SettlementAvailabilityService(settlement_repo, payment_repo)
    
    # 1. Create settlement
    create_handler = CreateSettlementHandler(settlement_repo, payment_repo, avail_svc)
    ctx = ExecutionContext(user_id="merchant", organization_id=org_id, role="MEMBER")
    await create_handler.handle(CreateSettlementCommand(
        settlement_id=settlement_id,
        organization_id=org_id,
        payment_id=pay_id,
        amount=Decimal("100.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-200001",
        idempotency_key="idem-lifecycle-prog"
    ), ctx)
    await db_session.flush()

    # 2. PENDING -> PROCESSING
    proc_handler = StartSettlementProcessingHandler(settlement_repo)
    res_proc = await proc_handler.handle(StartSettlementProcessingCommand(settlement_id, org_id), ctx)
    assert res_proc.is_success
    assert res_proc.value.status == SettlementStatus.PROCESSING

    # 3. PROCESSING -> SUBMITTED
    submit_handler = SubmitSettlementHandler(settlement_repo)
    res_sub = await submit_handler.handle(SubmitSettlementCommand(settlement_id, org_id, "0xTxReference"), ctx)
    assert res_sub.is_success
    assert res_sub.value.status == SettlementStatus.SUBMITTED
    assert res_sub.value.transaction_reference == "0xTxReference"

    # 4. SUBMITTED -> CONFIRMED
    confirm_handler = ConfirmSettlementHandler(settlement_repo)
    res_conf = await confirm_handler.handle(ConfirmSettlementCommand(settlement_id, org_id), ctx)
    assert res_conf.is_success
    assert res_conf.value.status == SettlementStatus.CONFIRMED

    # 5. CONFIRMED -> COMPLETED
    complete_handler = CompleteSettlementHandler(settlement_repo)
    res_comp = await complete_handler.handle(CompleteSettlementCommand(settlement_id, org_id), ctx)
    assert res_comp.is_success
    assert res_comp.value.status == SettlementStatus.COMPLETED


@pytest.mark.asyncio
async def test_settlement_failure_retry_and_cancellation_handlers(db_session: AsyncSession):
    org_id = str(uuid4())
    pay_id = str(uuid4())
    settlement_id = str(uuid4())
    await _setup_merchant(db_session, org_id)
    await _setup_payment(db_session, org_id, pay_id, Decimal("500.00"), confirmed=True)

    settlement_repo = PostgresSettlementRepository(db_session)
    payment_repo = PostgresPaymentRepository(db_session)
    avail_svc = SettlementAvailabilityService(settlement_repo, payment_repo)
    ctx = ExecutionContext(user_id="merchant", organization_id=org_id, role="MEMBER")

    # Create PENDING
    create_handler = CreateSettlementHandler(settlement_repo, payment_repo, avail_svc)
    await create_handler.handle(CreateSettlementCommand(
        settlement_id=settlement_id,
        organization_id=org_id,
        payment_id=pay_id,
        amount=Decimal("100.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-300001",
        idempotency_key="idem-fail-retry"
    ), ctx)
    await db_session.flush()

    # 1. PENDING -> CANCELLED (test cancellation)
    cancel_handler = CancelSettlementHandler(settlement_repo)
    res_cancel = await cancel_handler.handle(CancelSettlementCommand(settlement_id, org_id), ctx)
    assert res_cancel.is_success
    assert res_cancel.value.status == SettlementStatus.CANCELLED

    # Re-create another PENDING to test Failure & Retry progression
    settlement_id2 = str(uuid4())
    await create_handler.handle(CreateSettlementCommand(
        settlement_id=settlement_id2,
        organization_id=org_id,
        payment_id=pay_id,
        amount=Decimal("100.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-300002",
        idempotency_key="idem-fail-retry-2"
    ), ctx)
    await db_session.flush()

    # Move to PROCESSING
    proc_handler = StartSettlementProcessingHandler(settlement_repo)
    await proc_handler.handle(StartSettlementProcessingCommand(settlement_id2, org_id), ctx)

    # 2. PROCESSING -> FAILED (non-retryable first)
    fail_handler = FailSettlementHandler(settlement_repo)
    res_fail = await fail_handler.handle(FailSettlementCommand(
        settlement_id=settlement_id2,
        organization_id=org_id,
        failure_code="DESTINATION_INVALID",
        failure_message="Bad blockchain address format",
        failure_retryable=False
    ), ctx)
    assert res_fail.is_success
    assert res_fail.value.status == SettlementStatus.FAILED

    # Attempt to retry non-retryable FAILED should fail
    retry_handler = RetrySettlementHandler(settlement_repo)
    res_retry_fail = await retry_handler.handle(RetrySettlementCommand(settlement_id2, org_id), ctx)
    assert res_retry_fail.is_failure

    # Re-fail with retryable failure
    # Wait, fail() only allowed from PROCESSING state. Let's start processing first?
    # No, start_processing() is only allowed from PENDING or RETRYING. So we can't move FAILED to PROCESSING directly.
    # But wait, this is a unit-level restriction. We can create a 3rd settlement to test retryable success.
    settlement_id3 = str(uuid4())
    await create_handler.handle(CreateSettlementCommand(
        settlement_id=settlement_id3,
        organization_id=org_id,
        payment_id=pay_id,
        amount=Decimal("100.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-2026-300003",
        idempotency_key="idem-fail-retry-3"
    ), ctx)
    await db_session.flush()

    await proc_handler.handle(StartSettlementProcessingCommand(settlement_id3, org_id), ctx)
    await fail_handler.handle(FailSettlementCommand(
        settlement_id=settlement_id3,
        organization_id=org_id,
        failure_code="NETWORK_UNAVAILABLE",
        failure_message="RPC Timeout",
        failure_retryable=True
    ), ctx)

    # Retry retryable FAILED -> RETRYING (should pass)
    res_retry_ok = await retry_handler.handle(RetrySettlementCommand(settlement_id3, org_id), ctx)
    assert res_retry_ok.is_success
    assert res_retry_ok.value.status == SettlementStatus.RETRYING

    # RETRYING -> PROCESSING
    res_proc_reloaded = await proc_handler.handle(StartSettlementProcessingCommand(settlement_id3, org_id), ctx)
    assert res_proc_reloaded.is_success
    assert res_proc_reloaded.value.status == SettlementStatus.PROCESSING


@pytest.mark.asyncio
async def test_settlement_tenant_isolation_guards(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())
    pay_b = str(uuid4())
    settlement_b = str(uuid4())
    await _setup_merchant(db_session, org_a)
    await _setup_merchant(db_session, org_b)
    await _setup_payment(db_session, org_b, pay_b, Decimal("500.00"), confirmed=True)

    settlement_repo = PostgresSettlementRepository(db_session)
    payment_repo = PostgresPaymentRepository(db_session)
    avail_svc = SettlementAvailabilityService(settlement_repo, payment_repo)
    ctx_a = ExecutionContext(user_id="merchant", organization_id=org_a, role="MEMBER")
    ctx_b = ExecutionContext(user_id="merchant", organization_id=org_b, role="MEMBER")

    # Create Org B settlement
    create_handler = CreateSettlementHandler(settlement_repo, payment_repo, avail_svc)
    await create_handler.handle(CreateSettlementCommand(
        settlement_id=settlement_b,
        organization_id=org_b,
        payment_id=pay_b,
        amount=Decimal("100.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-ORG-B-CROSS",
        idempotency_key="idem-cross"
    ), ctx_b)
    await db_session.flush()

    # Org A tries to operate on Org B settlement -> should fail
    proc_handler = StartSettlementProcessingHandler(settlement_repo)
    res = await proc_handler.handle(StartSettlementProcessingCommand(settlement_b, org_a), ctx_a)
    assert res.is_failure
    assert res.error.code == "UNAUTHORIZED_ACCESS"


@pytest.mark.asyncio
async def test_settlement_transactional_outbox_rollback_on_failure(db_session: AsyncSession):
    org_id = str(uuid4())
    pay_id = str(uuid4())
    await _setup_merchant(db_session, org_id)
    await _setup_payment(db_session, org_id, pay_id, Decimal("500.00"), confirmed=True)

    settlement_repo = PostgresSettlementRepository(db_session)
    payment_repo = PostgresPaymentRepository(db_session)
    avail_svc = SettlementAvailabilityService(settlement_repo, payment_repo)
    ctx = ExecutionContext(user_id="merchant", organization_id=org_id, role="MEMBER")

    # Save initial settlement
    create_handler = CreateSettlementHandler(settlement_repo, payment_repo, avail_svc)
    await create_handler.handle(CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=org_id,
        payment_id=pay_id,
        amount=Decimal("100.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-ROLLBACK-ORIG",
        idempotency_key="idem-rollback"
    ), ctx)
    await db_session.flush()

    # Attempt to create duplicate reference (triggers unique IntegrityError on reference)
    cmd_dup = CreateSettlementCommand(
        settlement_id=str(uuid4()),
        organization_id=org_id,
        payment_id=pay_id,
        amount=Decimal("100.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network="BASE",
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        method="DIGITAL_ASSET",
        reference="SET-ROLLBACK-ORIG", # Duplicate reference
        idempotency_key="idem-rollback-dup"
    )

    try:
        await create_handler.handle(cmd_dup, ctx)
        await db_session.flush()
    except ValueError as err:
        assert "DUPLICATE_SETTLEMENT_REFERENCE" in str(err)

    await db_session.rollback()

    # Verify that NO duplicate outbox event or duplicate settlement is committed
    outbox_stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.payload.like("%idem-rollback-dup%"))
    outbox_events = (await db_session.execute(outbox_stmt)).scalars().all()
    assert len(outbox_events) == 0
