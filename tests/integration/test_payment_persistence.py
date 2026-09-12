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
    UserId,
    OrganizationSettings,
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
    Payment
)
from nomadx.src.infrastructure.database.repositories import (
    PostgresOrganizationRepository,
    PostgresInvoiceRepository,
    PostgresPaymentIntentRepository,
    PostgresPaymentRepository
)
from nomadx.src.infrastructure.database.models import OutboxEventDbModel, PaymentIntentDbModel, PaymentDbModel


async def _setup_merchant_and_invoice(session: AsyncSession, org_id_str: str, inv_id_str: str) -> Invoice:
    org_repo = PostgresOrganizationRepository(session)
    org = Organization.create(
        id=OrganizationId(org_id_str),
        name=OrganizationName("Nomad Corp"),
        slug=OrganizationSlug(f"nomad-corp-{org_id_str[:8]}"),
        owner_id=UserId(str(uuid4())),
        settings=OrganizationSettings("USD")
    ).value
    await org_repo.save(org)

    invoice_repo = PostgresInvoiceRepository(session)
    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    billing = BillingSnapshot(
        legal_name="Client Corp",
        email="client@corp.com",
        address="456 Wall St",
        country="US",
        tax_identifier="TAX-INT-01"
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
async def test_payment_intent_persistence_operations(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    await _setup_merchant_and_invoice(db_session, org_id, inv_id)

    intent_repo = PostgresPaymentIntentRepository(db_session)
    
    # Create domain intent
    usd_currency = Currency("USD", CurrencyType.FIAT)
    requested_amount = Money(Decimal("1234.56"), usd_currency) # High-precision
    asset = PaymentAsset("USD", "FIAT", 2)
    destination = PaymentDestination("BANK_ACCOUNT", "DE1234567890")
    
    intent_id = PaymentIntentId(str(uuid4()))
    intent = PaymentIntent.create(
        id=intent_id,
        organization_id=OrganizationId(org_id),
        invoice_id=InvoiceId(inv_id),
        reference=PaymentReference("PAY-INT-PER-1"),
        requested_amount=requested_amount,
        requested_asset=asset,
        destination=destination,
        payment_method=PaymentMethod.BANK_TRANSFER,
        expires_at=Clock.now() + timedelta(days=5)
    ).value

    # Save to db
    await intent_repo.save(intent)
    await db_session.flush()

    # Reconstruct and assert
    reconstructed = await intent_repo.find_by_id(intent_id)
    assert reconstructed is not None
    assert reconstructed.id == intent_id
    assert reconstructed.requested_amount.amount == Decimal("1234.56")
    assert reconstructed.requested_amount.currency.code == "USD"
    assert reconstructed.requested_asset.type == "FIAT"
    assert reconstructed.destination.identifier == "DE1234567890"

    # Query lists
    org_intents = await intent_repo.find_by_organization(OrganizationId(org_id))
    assert len(org_intents) == 1
    assert org_intents[0].id == intent_id

    invoice_intents = await intent_repo.find_by_invoice(InvoiceId(inv_id))
    assert len(invoice_intents) == 1
    assert invoice_intents[0].id == intent_id


@pytest.mark.asyncio
async def test_payment_persistence_and_duplicate_constraints(db_session: AsyncSession):
    org_id = str(uuid4())
    inv_id = str(uuid4())
    await _setup_merchant_and_invoice(db_session, org_id, inv_id)

    payment_repo = PostgresPaymentRepository(db_session)

    # Reconstruct test payment
    usdc_currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    observed_amount = Money(Decimal("50000000000000.123456"), usdc_currency) # High-precision decimal
    asset = PaymentAsset("USDC", "DIGITAL_ASSET", 6)
    network = PaymentNetwork("BASE", "BLOCKCHAIN")

    payment_id = PaymentId(str(uuid4()))
    payment = Payment.create(
        id=payment_id,
        organization_id=OrganizationId(org_id),
        payment_intent_id=None,
        observed_amount=observed_amount,
        asset=asset,
        network=network,
        transaction_reference="0xUniqueTxHash",
        sender="0xSenderWallet",
        receiver="0xReceiverWallet",
        observed_at=Clock.now()
    ).value

    # Save payment
    await payment_repo.save(payment)
    await db_session.flush()

    # Reconstruct
    reconstructed = await payment_repo.find_by_id(payment_id)
    assert reconstructed is not None
    assert reconstructed.id == payment_id
    assert reconstructed.observed_amount.amount == Decimal("50000000000000.123456")
    assert reconstructed.network.code == "BASE"
    assert reconstructed.transaction_reference == "0xUniqueTxHash"

    # Attempt to insert a concurrent duplicate (with same network + transaction_reference)
    payment_dup = Payment.create(
        id=PaymentId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        payment_intent_id=None,
        observed_amount=observed_amount,
        asset=asset,
        network=network,
        transaction_reference="0xUniqueTxHash", # Duplicate ref
        sender="0xOtherSender",
        receiver="0xReceiverWallet",
        observed_at=Clock.now()
    ).value

    # Execute and verify duplicate rejection
    async with db_session.begin_nested():
        with pytest.raises(ValueError) as exc:
            await payment_repo.save(payment_dup)
            await db_session.flush()
    assert "PAYMENT_ALREADY_EXISTS" in str(exc.value)

    # Verify that outbox event for duplicate payment was rolled back and NOT saved in session
    result = await db_session.execute(
        select(OutboxEventDbModel).where(OutboxEventDbModel.event_type == "PaymentDetected")
    )
    events = result.scalars().all()
    # Only 1 (the first successful detection) event should be persisted
    assert len(events) == 1
