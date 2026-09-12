import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from uuid import uuid4
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
    PaymentStatus,
    ReconciliationStatus
)

from nomadx.src.infrastructure.database.repositories import (
    PostgresOrganizationRepository,
    PostgresInvoiceRepository,
    PostgresPaymentIntentRepository,
    PostgresPaymentRepository,
    PostgresPaymentAllocationRepository
)
from nomadx.src.presentation.api.dependencies.context import ExecutionContext

from nomadx.src.application.payment.commands import (
    CreatePaymentIntentCommand,
    CreatePaymentIntentHandler,
    ActivatePaymentIntentCommand,
    ActivatePaymentIntentHandler,
    DetectPaymentCommand,
    DetectPaymentHandler,
    ReconcilePaymentCommand,
    ReconcilePaymentHandler
)


async def _create_test_env(
    session: AsyncSession,
    org_id_str: str,
    invoice_id_str: str,
    invoice_amount: Decimal,
    asset_code: str = "USDC"
) -> Invoice:
    # Organization
    org_repo = PostgresOrganizationRepository(session)
    org = Organization.create(
        id=OrganizationId(org_id_str),
        name=OrganizationName("Reconciliation Corp"),
        slug=OrganizationSlug(f"recon-{org_id_str[:8]}"),
        owner_id=UserId(str(uuid4())),
        settings=OrganizationSettings(asset_code)
    ).value
    await org_repo.save(org)

    # Invoice
    invoice_repo = PostgresInvoiceRepository(session)
    currency = Currency(asset_code, CurrencyType.DIGITAL_ASSET)
    billing = BillingSnapshot("Legal Entity", "legal@corp.com", "123 Street", "US", "TAX-99")
    
    invoice = Invoice.create(
        id=InvoiceId(invoice_id_str),
        organization_id=org.id,
        origin=InvoiceOrigin(InvoiceOriginType.NATIVE),
        currency=currency,
        payment_terms=PaymentTerms(PaymentTermsType.NET_30),
        customer_id=CustomerId(str(uuid4())),
        billing_snapshot=billing
    ).value
    
    # Add a line item to represent total amount
    # Subtotal = quantity * unit_price
    from nomadx.src.domain.invoice.line_item import InvoiceLineItem, LineItemId
    unit_price = Money(invoice_amount, currency)
    line_item = InvoiceLineItem(
        id=LineItemId(str(uuid4())),
        description="Consulting Service",
        quantity=Decimal("1"),
        unit_price=unit_price
    )
    invoice.add_line_item(line_item)
    
    from nomadx.src.domain.invoice.value_objects import InvoiceNumber
    invoice.issue(InvoiceNumber("INV-RECON-100"), Clock.now())
    await invoice_repo.save(invoice)
    await session.flush()
    return invoice


@pytest.mark.asyncio
async def test_reconciliation_exact_payment(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    await _create_test_env(db_session, org_id, inv_id, Decimal("5000.00"))

    # Create Payment Intent
    intent_id = str(uuid4())
    intent_repo = PostgresPaymentIntentRepository(db_session)
    invoice_repo = PostgresInvoiceRepository(db_session)
    
    cmd_intent = CreatePaymentIntentCommand(
        payment_intent_id=intent_id,
        invoice_id=inv_id,
        payment_reference="PAY-REF-EXACT",
        requested_amount=Decimal("5000.00"),
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
    res_intent = await CreatePaymentIntentHandler(invoice_repo, intent_repo).handle(cmd_intent, ctx)
    assert res_intent.is_success, f"Create intent failed: {res_intent.error.message if res_intent.is_failure else ''}"
    await db_session.flush()
    res_activate = await ActivatePaymentIntentHandler(intent_repo).handle(ActivatePaymentIntentCommand(intent_id), ctx)
    assert res_activate.is_success, f"Activate intent failed: {res_activate.error.message if res_activate.is_failure else ''}"
    await db_session.flush()

    # Detect & Confirm Payment
    payment_id = str(uuid4())
    payment_repo = PostgresPaymentRepository(db_session)
    cmd_detect = DetectPaymentCommand(
        payment_id=payment_id,
        payment_intent_id=intent_id,
        observed_amount=Decimal("5000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network_code="BASE",
        network_type="BLOCKCHAIN",
        transaction_reference="0xTxExact",
        sender="0xSender",
        receiver="0xMerchant",
        observed_at=Clock.now(),
        organization_id=org_id
    )
    res_detect = await DetectPaymentHandler(payment_repo, intent_repo).handle(cmd_detect, ctx)
    assert res_detect.is_success, f"Detect payment failed: {res_detect.error.message if res_detect.is_failure else ''}"
    
    # Confirm directly (so it is CONFIRMED and ready for reconciliation)
    payment = await payment_repo.find_by_id(PaymentId(payment_id))
    assert payment is not None, "Payment not found in repository after detect"
    payment.confirm()
    await payment_repo.save(payment)
    await db_session.flush()

    # Reconcile
    allocation_repo = PostgresPaymentAllocationRepository(db_session)
    handler = ReconcilePaymentHandler(payment_repo, intent_repo, invoice_repo, allocation_repo)
    
    res = await handler.handle(ReconcilePaymentCommand(payment_id, org_id), ctx)
    assert res.is_success
    assert res.value["status"] == "FULLY_ALLOCATED"
    assert res.value["allocated_amount"] == "5000.00"
    assert res.value["payment_remaining"] == "0.00"
    assert res.value["invoice_remaining"] == "0.00"

    # Verify database state
    saved_payment = await payment_repo.find_by_id(PaymentId(payment_id))
    assert saved_payment.reconciliation_status == ReconciliationStatus.FULLY_ALLOCATED

    allocs = await allocation_repo.find_by_payment_id(PaymentId(payment_id))
    assert len(allocs) == 1
    assert allocs[0].allocated_amount.amount == Decimal("5000.00")


@pytest.mark.asyncio
async def test_reconciliation_partial_payment(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    await _create_test_env(db_session, org_id, inv_id, Decimal("5000.00"))

    # Intent
    intent_id = str(uuid4())
    intent_repo = PostgresPaymentIntentRepository(db_session)
    invoice_repo = PostgresInvoiceRepository(db_session)
    
    cmd_intent = CreatePaymentIntentCommand(
        payment_intent_id=intent_id,
        invoice_id=inv_id,
        payment_reference="PAY-REF-PARTIAL",
        requested_amount=Decimal("5000.00"),
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
    res_intent = await CreatePaymentIntentHandler(invoice_repo, intent_repo).handle(cmd_intent, ctx)
    assert res_intent.is_success, f"Create intent failed: {res_intent.error.message if res_intent.is_failure else ''}"
    await db_session.flush()
    res_activate = await ActivatePaymentIntentHandler(intent_repo).handle(ActivatePaymentIntentCommand(intent_id), ctx)
    assert res_activate.is_success, f"Activate intent failed: {res_activate.error.message if res_activate.is_failure else ''}"
    await db_session.flush()

    # Detect & Confirm Partial Payment ($2,000)
    payment_id = str(uuid4())
    payment_repo = PostgresPaymentRepository(db_session)
    cmd_detect = DetectPaymentCommand(
        payment_id=payment_id,
        payment_intent_id=intent_id,
        observed_amount=Decimal("2000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network_code="BASE",
        network_type="BLOCKCHAIN",
        transaction_reference="0xTxPartial",
        sender="0xSender",
        receiver="0xMerchant",
        observed_at=Clock.now(),
        organization_id=org_id
    )
    res_detect = await DetectPaymentHandler(payment_repo, intent_repo).handle(cmd_detect, ctx)
    assert res_detect.is_success, f"Detect payment failed: {res_detect.error.message if res_detect.is_failure else ''}"
    
    payment = await payment_repo.find_by_id(PaymentId(payment_id))
    assert payment is not None, "Payment not found in repository after detect"
    payment.confirm()
    await payment_repo.save(payment)
    await db_session.flush()

    allocation_repo = PostgresPaymentAllocationRepository(db_session)
    handler = ReconcilePaymentHandler(payment_repo, intent_repo, invoice_repo, allocation_repo)
    
    res = await handler.handle(ReconcilePaymentCommand(payment_id, org_id), ctx)
    assert res.is_success
    assert res.value["status"] == "PARTIALLY_ALLOCATED"
    assert res.value["allocated_amount"] == "2000.00"
    assert res.value["payment_remaining"] == "0.00"
    assert res.value["invoice_remaining"] == "3000.00"


@pytest.mark.asyncio
async def test_reconciliation_overpayment(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    await _create_test_env(db_session, org_id, inv_id, Decimal("5000.00"))

    # Intent
    intent_id = str(uuid4())
    intent_repo = PostgresPaymentIntentRepository(db_session)
    invoice_repo = PostgresInvoiceRepository(db_session)
    
    cmd_intent = CreatePaymentIntentCommand(
        payment_intent_id=intent_id,
        invoice_id=inv_id,
        payment_reference="PAY-REF-OVER",
        requested_amount=Decimal("5000.00"),
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
    res_intent = await CreatePaymentIntentHandler(invoice_repo, intent_repo).handle(cmd_intent, ctx)
    assert res_intent.is_success, f"Create intent failed: {res_intent.error.message if res_intent.is_failure else ''}"
    await db_session.flush()
    res_activate = await ActivatePaymentIntentHandler(intent_repo).handle(ActivatePaymentIntentCommand(intent_id), ctx)
    assert res_activate.is_success, f"Activate intent failed: {res_activate.error.message if res_activate.is_failure else ''}"
    await db_session.flush()

    # Detect & Confirm Overpayment ($6,000)
    payment_id = str(uuid4())
    payment_repo = PostgresPaymentRepository(db_session)
    cmd_detect = DetectPaymentCommand(
        payment_id=payment_id,
        payment_intent_id=intent_id,
        observed_amount=Decimal("6000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network_code="BASE",
        network_type="BLOCKCHAIN",
        transaction_reference="0xTxOver",
        sender="0xSender",
        receiver="0xMerchant",
        observed_at=Clock.now(),
        organization_id=org_id
    )
    res_detect = await DetectPaymentHandler(payment_repo, intent_repo).handle(cmd_detect, ctx)
    assert res_detect.is_success, f"Detect payment failed: {res_detect.error.message if res_detect.is_failure else ''}"
    
    payment = await payment_repo.find_by_id(PaymentId(payment_id))
    assert payment is not None, "Payment not found in repository after detect"
    payment.confirm()
    await payment_repo.save(payment)
    await db_session.flush()
    allocation_repo = PostgresPaymentAllocationRepository(db_session)
    handler = ReconcilePaymentHandler(payment_repo, intent_repo, invoice_repo, allocation_repo)
    
    res = await handler.handle(ReconcilePaymentCommand(payment_id, org_id), ctx)
    assert res.is_success
    assert res.value["status"] == "OVERPAYMENT"
    assert res.value["allocated_amount"] == "5000.00"
    assert res.value["payment_remaining"] == "1000.00"
    assert res.value["invoice_remaining"] == "0.00"


@pytest.mark.asyncio
async def test_reconciliation_late_payment_expired_intent(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    await _create_test_env(db_session, org_id, inv_id, Decimal("5000.00"))

    # Intent
    intent_id = str(uuid4())
    intent_repo = PostgresPaymentIntentRepository(db_session)
    invoice_repo = PostgresInvoiceRepository(db_session)
    
    cmd_intent = CreatePaymentIntentCommand(
        payment_intent_id=intent_id,
        invoice_id=inv_id,
        payment_reference="PAY-REF-LATE",
        requested_amount=Decimal("5000.00"),
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
    res_intent = await CreatePaymentIntentHandler(invoice_repo, intent_repo).handle(cmd_intent, ctx)
    assert res_intent.is_success, f"Create intent failed: {res_intent.error.message if res_intent.is_failure else ''}"
    await db_session.flush()
    res_activate = await ActivatePaymentIntentHandler(intent_repo).handle(ActivatePaymentIntentCommand(intent_id), ctx)
    assert res_activate.is_success, f"Activate intent failed: {res_activate.error.message if res_activate.is_failure else ''}"
    await db_session.flush()

    # Expire intent manually
    intent = await intent_repo.find_by_id(PaymentIntentId(intent_id))
    assert intent is not None, "Intent not found in repository before expire"
    intent.expire()
    await intent_repo.save(intent)
    await db_session.flush()

    # Confirm Payment manually
    payment_id = str(uuid4())
    payment_repo = PostgresPaymentRepository(db_session)
    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    payment = Payment.create(
        id=PaymentId(payment_id),
        organization_id=OrganizationId(org_id),
        payment_intent_id=PaymentIntentId(intent_id),
        observed_amount=Money(Decimal("5000.00"), currency),
        asset=PaymentAsset("USDC", "DIGITAL_ASSET", 6),
        network=PaymentNetwork("BASE", "BLOCKCHAIN"),
        transaction_reference="0xTxLate",
        sender="0xSender",
        receiver="0xMerchant",
        observed_at=Clock.now()
    ).value
    payment.confirm()
    await payment_repo.save(payment)
    await db_session.flush()

    allocation_repo = PostgresPaymentAllocationRepository(db_session)
    handler = ReconcilePaymentHandler(payment_repo, intent_repo, invoice_repo, allocation_repo)
    
    # Try to reconcile - should fail with late payment exception
    res = await handler.handle(ReconcilePaymentCommand(payment_id, org_id), ctx)
    assert res.is_failure
    
    # Verify exception is logged on payment
    saved_payment = await payment_repo.find_by_id(PaymentId(payment_id))
    assert saved_payment.reconciliation_status == ReconciliationStatus.EXCEPTION


@pytest.mark.asyncio
async def test_reconciliation_unconfirmed_payment_fails(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    await _create_test_env(db_session, org_id, inv_id, Decimal("5000.00"))

    # Intent
    intent_id = str(uuid4())
    intent_repo = PostgresPaymentIntentRepository(db_session)
    invoice_repo = PostgresInvoiceRepository(db_session)
    
    cmd_intent = CreatePaymentIntentCommand(
        payment_intent_id=intent_id,
        invoice_id=inv_id,
        payment_reference="PAY-REF-UNCONF",
        requested_amount=Decimal("5000.00"),
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
    res_intent = await CreatePaymentIntentHandler(invoice_repo, intent_repo).handle(cmd_intent, ctx)
    assert res_intent.is_success, f"Create intent failed: {res_intent.error.message if res_intent.is_failure else ''}"
    await db_session.flush()
    res_activate = await ActivatePaymentIntentHandler(intent_repo).handle(ActivatePaymentIntentCommand(intent_id), ctx)
    assert res_activate.is_success, f"Activate intent failed: {res_activate.error.message if res_activate.is_failure else ''}"
    await db_session.flush()

    # Detect Payment (leaves status in DETECTED)
    payment_id = str(uuid4())
    payment_repo = PostgresPaymentRepository(db_session)
    cmd_detect = DetectPaymentCommand(
        payment_id=payment_id,
        payment_intent_id=intent_id,
        observed_amount=Decimal("5000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network_code="BASE",
        network_type="BLOCKCHAIN",
        transaction_reference="0xTxUnconf",
        sender="0xSender",
        receiver="0xMerchant",
        observed_at=Clock.now(),
        organization_id=org_id
    )
    res_detect = await DetectPaymentHandler(payment_repo, intent_repo).handle(cmd_detect, ctx)
    assert res_detect.is_success, f"Detect payment failed: {res_detect.error.message if res_detect.is_failure else ''}"
    await db_session.flush()

    allocation_repo = PostgresPaymentAllocationRepository(db_session)
    handler = ReconcilePaymentHandler(payment_repo, intent_repo, invoice_repo, allocation_repo)
    
    # Try reconciling DETECTED payment - should fail
    res = await handler.handle(ReconcilePaymentCommand(payment_id, org_id), ctx)
    assert res.is_failure
    assert "confirmed payments" in res.error.message


@pytest.mark.asyncio
async def test_reconciliation_tenant_isolation(db_session: AsyncSession):
    org_a = str(uuid4())
    org_b = str(uuid4())
    inv_id = str(uuid4())
    await _create_test_env(db_session, org_a, inv_id, Decimal("5000.00"))

    # Intent
    intent_id = str(uuid4())
    intent_repo = PostgresPaymentIntentRepository(db_session)
    invoice_repo = PostgresInvoiceRepository(db_session)
    
    cmd_intent = CreatePaymentIntentCommand(
        payment_intent_id=intent_id,
        invoice_id=inv_id,
        payment_reference="PAY-REF-TENANT",
        requested_amount=Decimal("5000.00"),
        requested_asset_code="USDC",
        requested_asset_type="DIGITAL_ASSET",
        requested_asset_decimals=6,
        destination_type="BLOCKCHAIN_ADDRESS",
        destination_identifier="0xMerchant",
        destination_network="BASE",
        payment_method="DIGITAL_ASSET",
        expires_at=Clock.now() + timedelta(days=1),
        organization_id=org_a
    )
    ctx_a = ExecutionContext(user_id=str(uuid4()), organization_id=org_a, role="OWNER")
    res_intent = await CreatePaymentIntentHandler(invoice_repo, intent_repo).handle(cmd_intent, ctx_a)
    assert res_intent.is_success, f"Create intent failed: {res_intent.error.message if res_intent.is_failure else ''}"
    await db_session.flush()
    res_activate = await ActivatePaymentIntentHandler(intent_repo).handle(ActivatePaymentIntentCommand(intent_id), ctx_a)
    assert res_activate.is_success, f"Activate intent failed: {res_activate.error.message if res_activate.is_failure else ''}"
    await db_session.flush()

    # Detect & Confirm Payment in Org A
    payment_id = str(uuid4())
    payment_repo = PostgresPaymentRepository(db_session)
    cmd_detect = DetectPaymentCommand(
        payment_id=payment_id,
        payment_intent_id=intent_id,
        observed_amount=Decimal("5000.00"),
        asset_code="USDC",
        asset_type="DIGITAL_ASSET",
        asset_decimals=6,
        network_code="BASE",
        network_type="BLOCKCHAIN",
        transaction_reference="0xTxTenant",
        sender="0xSender",
        receiver="0xMerchant",
        observed_at=Clock.now(),
        organization_id=org_a
    )
    res_detect = await DetectPaymentHandler(payment_repo, intent_repo).handle(cmd_detect, ctx_a)
    assert res_detect.is_success, f"Detect payment failed: {res_detect.error.message if res_detect.is_failure else ''}"
    
    payment = await payment_repo.find_by_id(PaymentId(payment_id))
    assert payment is not None, "Payment not found in repository after detect"
    payment.confirm()
    await payment_repo.save(payment)
    await db_session.flush()

    allocation_repo = PostgresPaymentAllocationRepository(db_session)
    handler = ReconcilePaymentHandler(payment_repo, intent_repo, invoice_repo, allocation_repo)
    
    # Try reconciling Org A's payment with Org B's context - should fail
    ctx_b = ExecutionContext(user_id=str(uuid4()), organization_id=org_b, role="OWNER")
    res = await handler.handle(ReconcilePaymentCommand(payment_id, org_a), ctx_b)
    assert res.is_failure
