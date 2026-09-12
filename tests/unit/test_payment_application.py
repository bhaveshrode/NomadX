import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.organization import Organization
from nomadx.src.domain.organization.value_objects import (
    OrganizationId,
    OrganizationName,
    OrganizationSlug,
    OrganizationSettings,
    UserId,
)
from nomadx.src.domain.invoice import InvoiceId, BillingSnapshot, CustomerId, Invoice, InvoiceOrigin, InvoiceOriginType, PaymentTerms, PaymentTermsType
from nomadx.src.domain.invoice.value_objects import Currency, CurrencyType, Money
from nomadx.src.domain.payment import (
    PaymentIntentId,
    PaymentId,
    PaymentReference,
    PaymentAsset,
    PaymentNetwork,
    PaymentDestination,
    PaymentMethod,
    PaymentIntent,
    PaymentIntentStatus,
    Payment,
    PaymentStatus
)

from nomadx.src.infrastructure.database.repositories import (
    PostgresOrganizationRepository,
    PostgresInvoiceRepository,
    PostgresPaymentIntentRepository,
    PostgresPaymentRepository
)
from nomadx.src.infrastructure.database.models import OutboxEventDbModel, PaymentIntentDbModel, PaymentDbModel

from nomadx.src.presentation.api.dependencies.context import ExecutionContext

from nomadx.src.application.payment.commands import (
    CreatePaymentIntentCommand,
    CreatePaymentIntentHandler,
    ActivatePaymentIntentCommand,
    ActivatePaymentIntentHandler,
    CancelPaymentIntentCommand,
    CancelPaymentIntentHandler,
    ExpirePaymentIntentCommand,
    ExpirePaymentIntentHandler,
    DetectPaymentCommand,
    DetectPaymentHandler,
    StartPaymentVerificationCommand,
    StartPaymentVerificationHandler,
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    RejectPaymentCommand,
    RejectPaymentHandler
)


async def _create_test_organization_and_invoice(session: AsyncSession, org_id_str: str, inv_id_str: str) -> Invoice:
    # Save Org
    org_repo = PostgresOrganizationRepository(session)
    org = Organization.create(
        id=OrganizationId(org_id_str),
        name=OrganizationName("Test Corp"),
        slug=OrganizationSlug(f"test-corp-{org_id_str[:8]}"),
        owner_id=UserId(str(uuid4())),
        settings=OrganizationSettings("USD")
    ).value
    await org_repo.save(org)

    # Save Invoice
    invoice_repo = PostgresInvoiceRepository(session)
    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    billing = BillingSnapshot(
        legal_name="Customer Ltd",
        email="cust@ltd.com",
        address="123 Main St",
        country="US",
        tax_identifier="TAX-001"
    )
    invoice = Invoice.create(
        id=InvoiceId(inv_id_str),
        organization_id=org.id,
        origin=InvoiceOrigin(InvoiceOriginType.NATIVE),
        currency=currency,
        payment_terms=PaymentTerms(PaymentTermsType.NET_30),
        customer_id=CustomerId(str(uuid4())),
        billing_snapshot=billing
    ).value
    await invoice_repo.save(invoice)
    await session.flush()
    return invoice


@pytest.mark.asyncio
async def test_payment_intent_application_flow(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    await _create_test_organization_and_invoice(db_session, org_id, inv_id)

    # 1. Create PaymentIntent
    intent_id = str(uuid4())
    cmd_create = CreatePaymentIntentCommand(
        payment_intent_id=intent_id,
        invoice_id=inv_id,
        payment_reference="PAY-REF-001",
        requested_amount=Decimal("100.00"),
        requested_asset_code="USDC",
        requested_asset_type="DIGITAL_ASSET",
        requested_asset_decimals=6,
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0xMerchantAddress",
        destination_network="BASE",
        payment_method="DIGITAL_ASSET",
        expires_at=Clock.now() + timedelta(days=2),
        organization_id=org_id
    )

    ctx_ok = ExecutionContext(user_id=str(uuid4()), organization_id=org_id, role="OWNER")
    
    invoice_repo = PostgresInvoiceRepository(db_session)
    intent_repo = PostgresPaymentIntentRepository(db_session)
    handler_create = CreatePaymentIntentHandler(invoice_repo, intent_repo)

    # Execute
    res_create = await handler_create.handle(cmd_create, ctx_ok)
    assert res_create.is_success
    await db_session.flush()

    # Verify intent saved
    saved_intent = await intent_repo.find_by_id(res_create.value.id)
    assert saved_intent is not None
    assert saved_intent.status == PaymentIntentStatus.CREATED
    assert saved_intent.requested_amount.amount == Decimal("100.00")

    # Verify outbox event created
    result_outbox = await db_session.execute(
        select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "PaymentIntentCreated")
    )
    events = result_outbox.scalars().all()
    assert len(events) == 1
    assert "PAY-REF-001" in events[0].payload or intent_id in events[0].payload

    # 2. Activate PaymentIntent
    handler_activate = ActivatePaymentIntentHandler(intent_repo)
    res_act = await handler_activate.handle(ActivatePaymentIntentCommand(payment_intent_id=intent_id), ctx_ok)
    assert res_act.is_success
    await db_session.flush()

    saved_intent = await intent_repo.find_by_id(res_create.value.id)
    assert saved_intent.status == PaymentIntentStatus.ACTIVE

    # 3. Cancel PaymentIntent
    handler_cancel = CancelPaymentIntentHandler(intent_repo)
    res_cancel = await handler_cancel.handle(CancelPaymentIntentCommand(payment_intent_id=intent_id), ctx_ok)
    assert res_cancel.is_success
    await db_session.flush()

    saved_intent = await intent_repo.find_by_id(res_create.value.id)
    assert saved_intent.status == PaymentIntentStatus.CANCELLED


@pytest.mark.asyncio
async def test_payment_intent_creation_tenant_mismatch(db_session: AsyncSession):
    org_a = str(uuid4())
    inv_a = str(uuid4())
    await _create_test_organization_and_invoice(db_session, org_a, inv_a)

    # Try creating with context for Org B (should fail)
    ctx_b = ExecutionContext(user_id=str(uuid4()), organization_id=str(uuid4()), role="OWNER")
    
    cmd_create = CreatePaymentIntentCommand(
        payment_intent_id=str(uuid4()),
        invoice_id=inv_a,
        payment_reference="PAY-REF-002",
        requested_amount=Decimal("100.00"),
        requested_asset_code="USDC",
        requested_asset_type="DIGITAL_ASSET",
        requested_asset_decimals=6,
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0xMerchantAddress",
        destination_network="BASE",
        payment_method="DIGITAL_ASSET",
        expires_at=Clock.now() + timedelta(days=2),
        organization_id=org_a
    )

    invoice_repo = PostgresInvoiceRepository(db_session)
    intent_repo = PostgresPaymentIntentRepository(db_session)
    handler_create = CreatePaymentIntentHandler(invoice_repo, intent_repo)

    res = await handler_create.handle(cmd_create, ctx_b)
    # Checks: Org B cannot access Org A's invoice or create an intent for it
    assert res.is_failure
    assert res.error.code in ("UNAUTHORIZED_ACCESS", "INVOICE_OWNERSHIP_MISMATCH")


@pytest.mark.asyncio
async def test_payment_detection_and_lifecycle_application_flow(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    await _create_test_organization_and_invoice(db_session, org_id, inv_id)

    # First, create and activate an intent
    intent_id = str(uuid4())
    intent_repo = PostgresPaymentIntentRepository(db_session)
    invoice_repo = PostgresInvoiceRepository(db_session)
    
    cmd_intent = CreatePaymentIntentCommand(
        payment_intent_id=intent_id,
        invoice_id=inv_id,
        payment_reference="PAY-REF-003",
        requested_amount=Decimal("50.00"),
        requested_asset_code="USDC",
        requested_asset_type="DIGITAL_ASSET",
        requested_asset_decimals=6,
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0xMerchant",
        destination_network="BASE",
        payment_method="DIGITAL_ASSET",
        expires_at=Clock.now() + timedelta(days=1),
        organization_id=org_id
    )
    ctx = ExecutionContext(user_id=str(uuid4()), organization_id=org_id, role="OWNER")
    await CreatePaymentIntentHandler(invoice_repo, intent_repo).handle(cmd_intent, ctx)
    await ActivatePaymentIntentHandler(intent_repo).handle(ActivatePaymentIntentCommand(intent_id), ctx)
    await db_session.flush()

    # Detect observed payment
    payment_id = str(uuid4())
    cmd_detect = DetectPaymentCommand(
        payment_id=payment_id,
        payment_intent_id=intent_id,
        observed_amount=Decimal("50.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network_code="BASE",
        network_type="BLOCKCHAIN",
        transaction_reference="0xTxHashConfirmed",
        sender="0xSenderWallet",
        receiver="0xMerchant",
        observed_at=Clock.now(),
        organization_id=org_id
    )

    payment_repo = PostgresPaymentRepository(db_session)
    handler_detect = DetectPaymentHandler(payment_repo, intent_repo)

    # 1. Detect
    res_detect = await handler_detect.handle(cmd_detect, ctx)
    assert res_detect.is_success
    await db_session.flush()

    saved_payment = await payment_repo.find_by_id(res_detect.value.id)
    assert saved_payment is not None
    assert saved_payment.status == PaymentStatus.DETECTED

    # Try detecting duplicate (should fail with duplicate error)
    res_dup = await handler_detect.handle(cmd_detect, ctx)
    assert res_dup.is_failure
    assert res_dup.error.code == "DUPLICATE_PAYMENT"

    # 2. Start Verification
    from nomadx.src.application.payment.verification import (
        BlockchainClientProvider,
        ConfirmationPolicy,
        PaymentVerificationService
    )
    from nomadx.src.infrastructure.blockchain.registry import BlockchainAssetRegistry
    from nomadx.src.infrastructure.blockchain.clients.fake import FakeBlockchainClient

    registry = BlockchainAssetRegistry()
    policy = ConfirmationPolicy()
    verification_service = PaymentVerificationService(registry, policy)

    client_provider = BlockchainClientProvider()
    fake_client = FakeBlockchainClient(current_height=1000)
    fake_client.add_simulated_receipt(
        tx_hash="0xTxHashConfirmed",
        status="0x1",
        block_number=950
    )
    client_provider.register_client("BASE", fake_client)

    handler_verify = StartPaymentVerificationHandler(
        payment_repo,
        intent_repo,
        client_provider,
        verification_service
    )
    res_verify = await handler_verify.handle(StartPaymentVerificationCommand(payment_id=payment_id), ctx)
    assert res_verify.is_success
    await db_session.flush()

    saved_payment = await payment_repo.find_by_id(saved_payment.id)
    assert saved_payment.status == PaymentStatus.CONFIRMED
