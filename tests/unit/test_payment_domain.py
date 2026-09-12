import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from nomadx.src.domain.shared.clock.clock import Clock, ControllableClock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import InvoiceId, Money, Currency, CurrencyType
from nomadx.src.domain.payment import (
    PaymentIntentId,
    PaymentId,
    PaymentReference,
    PaymentAsset,
    PaymentNetwork,
    PaymentDestination,
    PaymentMethod,
    PaymentIntentStatus,
    PaymentIntent,
    PaymentStatus,
    Payment
)


@pytest.fixture(autouse=True)
def cleanup_clock():
    Clock.reset()
    yield
    Clock.reset()


def test_payment_intent_creation_success():
    org_id = OrganizationId(str(uuid4()))
    inv_id = InvoiceId(str(uuid4()))
    ref = PaymentReference("PAY-2026-001")
    
    usd_currency = Currency("USD", CurrencyType.FIAT)
    amount = Money(Decimal("100.00"), usd_currency)
    
    asset = PaymentAsset("USD", "FIAT", 2)
    network = PaymentNetwork("SEPA", "BANK")
    destination = PaymentDestination("BANK_ACCOUNT", "IBAN-DE-12345", network.code)
    
    expires_at = Clock.now() + timedelta(days=1)
    
    intent_id = PaymentIntentId(str(uuid4()))
    result = PaymentIntent.create(
        id=intent_id,
        organization_id=org_id,
        invoice_id=inv_id,
        reference=ref,
        requested_amount=amount,
        requested_asset=asset,
        destination=destination,
        payment_method=PaymentMethod.BANK_TRANSFER,
        expires_at=expires_at
    )
    
    assert result.is_success
    intent = result.value
    assert intent.id == intent_id
    assert intent.organization_id == org_id
    assert intent.invoice_id == inv_id
    assert intent.reference == ref
    assert intent.requested_amount == amount
    assert intent.status == PaymentIntentStatus.CREATED
    assert intent.expires_at == expires_at
    assert len(intent.domain_events) == 1


def test_payment_intent_creation_invalid_amount_or_expiry():
    org_id = OrganizationId(str(uuid4()))
    inv_id = InvoiceId(str(uuid4()))
    ref = PaymentReference("PAY-2026-001")
    usd_currency = Currency("USD", CurrencyType.FIAT)
    
    asset = PaymentAsset("USD", "FIAT", 2)
    destination = PaymentDestination("BANK_ACCOUNT", "IBAN-DE-12345")
    
    # 1. Negative amount
    amount_neg = Money(Decimal("-50.00"), usd_currency)
    res_neg = PaymentIntent.create(
        id=PaymentIntentId(str(uuid4())),
        organization_id=org_id,
        invoice_id=inv_id,
        reference=ref,
        requested_amount=amount_neg,
        requested_asset=asset,
        destination=destination,
        payment_method=PaymentMethod.BANK_TRANSFER,
        expires_at=Clock.now() + timedelta(days=1)
    )
    assert res_neg.is_failure
    assert "Requested amount must be positive" in res_neg.error.message

    # 2. Expiry in the past
    amount_valid = Money(Decimal("100.00"), usd_currency)
    res_expiry = PaymentIntent.create(
        id=PaymentIntentId(str(uuid4())),
        organization_id=org_id,
        invoice_id=inv_id,
        reference=ref,
        requested_amount=amount_valid,
        requested_asset=asset,
        destination=destination,
        payment_method=PaymentMethod.BANK_TRANSFER,
        expires_at=Clock.now() - timedelta(minutes=1)
    )
    assert res_expiry.is_failure
    assert "Expiration time must be in the future" in res_expiry.error.message


def test_payment_intent_digital_asset_destination_guards():
    org_id = OrganizationId(str(uuid4()))
    inv_id = InvoiceId(str(uuid4()))
    ref = PaymentReference("PAY-2026-001")
    usdc_currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    amount = Money(Decimal("500.00"), usdc_currency)
    asset = PaymentAsset("USDC", "DIGITAL_ASSET", 6)
    
    # Missing network or wrong type for DIGITAL_ASSET method
    destination_invalid = PaymentDestination("BANK_ACCOUNT", "0xAddress")
    
    res = PaymentIntent.create(
        id=PaymentIntentId(str(uuid4())),
        organization_id=org_id,
        invoice_id=inv_id,
        reference=ref,
        requested_amount=amount,
        requested_asset=asset,
        destination=destination_invalid,
        payment_method=PaymentMethod.DIGITAL_ASSET,
        expires_at=Clock.now() + timedelta(days=1)
    )
    assert res.is_failure
    assert "Digital asset payments require a blockchain destination address" in res.error.message


def test_payment_intent_lifecycle_transitions():
    org_id = OrganizationId(str(uuid4()))
    inv_id = InvoiceId(str(uuid4()))
    ref = PaymentReference("PAY-2026-001")
    usd_currency = Currency("USD", CurrencyType.FIAT)
    amount = Money(Decimal("100.00"), usd_currency)
    asset = PaymentAsset("USD", "FIAT", 2)
    destination = PaymentDestination("BANK_ACCOUNT", "IBAN-DE-12345")
    
    intent = PaymentIntent.create(
        id=PaymentIntentId(str(uuid4())),
        organization_id=org_id,
        invoice_id=inv_id,
        reference=ref,
        requested_amount=amount,
        requested_asset=asset,
        destination=destination,
        payment_method=PaymentMethod.BANK_TRANSFER,
        expires_at=Clock.now() + timedelta(days=1)
    ).value

    # Cannot cancel when already CANCELLED, or expire when not ACTIVE
    assert intent.expire().is_failure

    # CREATED -> ACTIVE
    assert intent.activate().is_success
    assert intent.status == PaymentIntentStatus.ACTIVE
    
    # ACTIVE -> CANCELLED
    assert intent.cancel().is_success
    assert intent.status == PaymentIntentStatus.CANCELLED
    
    # State is final, cannot activate anymore
    assert intent.activate().is_failure


def test_payment_intent_activation_past_expiry_fails():
    org_id = OrganizationId(str(uuid4()))
    inv_id = InvoiceId(str(uuid4()))
    ref = PaymentReference("PAY-2026-001")
    usd_currency = Currency("USD", CurrencyType.FIAT)
    amount = Money(Decimal("100.00"), usd_currency)
    asset = PaymentAsset("USD", "FIAT", 2)
    destination = PaymentDestination("BANK_ACCOUNT", "IBAN-DE-12345")
    
    expires_at = Clock.now() + timedelta(minutes=10)
    intent = PaymentIntent.create(
        id=PaymentIntentId(str(uuid4())),
        organization_id=org_id,
        invoice_id=inv_id,
        reference=ref,
        requested_amount=amount,
        requested_asset=asset,
        destination=destination,
        payment_method=PaymentMethod.BANK_TRANSFER,
        expires_at=expires_at
    ).value

    # Freeze clock past expires_at
    Clock.set_provider(ControllableClock(expires_at + timedelta(seconds=1)))
    
    res = intent.activate()
    assert res.is_failure
    assert intent.status == PaymentIntentStatus.EXPIRED


def test_payment_detection_and_verification():
    org_id = OrganizationId(str(uuid4()))
    intent_id = PaymentIntentId(str(uuid4()))
    usdc_currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    amount = Money(Decimal("250.00"), usdc_currency)
    asset = PaymentAsset("USDC", "DIGITAL_ASSET", 6)
    network = PaymentNetwork("BASE", "BLOCKCHAIN")
    
    payment_id = PaymentId(str(uuid4()))
    res = Payment.create(
        id=payment_id,
        organization_id=org_id,
        payment_intent_id=intent_id,
        observed_amount=amount,
        asset=asset,
        network=network,
        transaction_reference="0xTxHash123",
        sender="0xSenderWallet",
        receiver="0xReceiverWallet",
        observed_at=Clock.now()
    )
    
    assert res.is_success
    payment = res.value
    assert payment.status == PaymentStatus.DETECTED
    assert payment.observed_amount == amount
    
    # 1. Start Verification
    assert payment.start_verification().is_success
    assert payment.status == PaymentStatus.PENDING_VERIFICATION
    
    # 2. Confirm
    assert payment.confirm().is_success
    assert payment.status == PaymentStatus.CONFIRMED


def test_payment_mismatched_asset_currency():
    org_id = OrganizationId(str(uuid4()))
    usdc_currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    
    # Mismatch: Amount in EUR but Asset is USDC
    amount = Money(Decimal("250.00"), Currency("EUR", CurrencyType.FIAT))
    asset = PaymentAsset("USDC", "DIGITAL_ASSET", 6)
    network = PaymentNetwork("BASE", "BLOCKCHAIN")
    
    res = Payment.create(
        id=PaymentId(str(uuid4())),
        organization_id=org_id,
        payment_intent_id=None,
        observed_amount=amount,
        asset=asset,
        network=network,
        transaction_reference="0xTxHash123",
        sender="0xSenderWallet",
        receiver="0xReceiverWallet",
        observed_at=Clock.now()
    )
    assert res.is_failure
    assert "does not match asset code" in res.error.message


def test_payment_business_scenarios():
    # Model a payment intent of $500
    org_id = OrganizationId(str(uuid4()))
    inv_id = InvoiceId(str(uuid4()))
    ref = PaymentReference("PAY-007")
    usdc_currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    requested_amount = Money(Decimal("500.00"), usdc_currency)
    asset = PaymentAsset("USDC", "DIGITAL_ASSET", 6)
    network = PaymentNetwork("BASE", "BLOCKCHAIN")
    destination = PaymentDestination("BLOCKCHAIN_ADDRESS", "0xMerchant", network.code)
    
    intent = PaymentIntent.create(
        id=PaymentIntentId(str(uuid4())),
        organization_id=org_id,
        invoice_id=inv_id,
        reference=ref,
        requested_amount=requested_amount,
        requested_asset=asset,
        destination=destination,
        payment_method=PaymentMethod.DIGITAL_ASSET,
        expires_at=Clock.now() + timedelta(hours=2)
    ).value

    intent.activate()

    # Scenario A: Partial Payment (Customer pays $200 instead of $500)
    partial_amount = Money(Decimal("200.00"), usdc_currency)
    res_partial = Payment.create(
        id=PaymentId(str(uuid4())),
        organization_id=org_id,
        payment_intent_id=intent.id,
        observed_amount=partial_amount,
        asset=asset,
        network=network,
        transaction_reference="0xTxHashPartial",
        sender="0xCustomer",
        receiver="0xMerchant",
        observed_at=Clock.now()
    )
    assert res_partial.is_success
    payment_partial = res_partial.value
    # Asserting that Payment creation does not mutate the Intent's requested amount
    assert payment_partial.observed_amount.amount == Decimal("200.00")
    assert intent.requested_amount.amount == Decimal("500.00")

    # Scenario B: Overpayment (Customer pays $600 instead of $500)
    over_amount = Money(Decimal("600.00"), usdc_currency)
    res_over = Payment.create(
        id=PaymentId(str(uuid4())),
        organization_id=org_id,
        payment_intent_id=intent.id,
        observed_amount=over_amount,
        asset=asset,
        network=network,
        transaction_reference="0xTxHashOver",
        sender="0xCustomer",
        receiver="0xMerchant",
        observed_at=Clock.now()
    )
    assert res_over.is_success
    payment_over = res_over.value
    assert payment_over.observed_amount.amount == Decimal("600.00")
    assert intent.requested_amount.amount == Decimal("500.00")

    # Scenario C: Late Payment after Intent Expiration
    intent_late = PaymentIntent.create(
        id=PaymentIntentId(str(uuid4())),
        organization_id=org_id,
        invoice_id=inv_id,
        reference=PaymentReference("PAY-008"),
        requested_amount=requested_amount,
        requested_asset=asset,
        destination=destination,
        payment_method=PaymentMethod.DIGITAL_ASSET,
        expires_at=Clock.now() + timedelta(minutes=5)
    ).value
    intent_late.activate()
    intent_late.expire()  # Expire the intent
    
    # Customer pays late
    late_payment = Payment.create(
        id=PaymentId(str(uuid4())),
        organization_id=org_id,
        payment_intent_id=intent_late.id,
        observed_amount=requested_amount,
        asset=asset,
        network=network,
        transaction_reference="0xLateTx",
        sender="0xCustomer",
        receiver="0xMerchant",
        observed_at=Clock.now() + timedelta(minutes=10)
    ).value
    
    assert late_payment.status == PaymentStatus.DETECTED
    assert intent_late.status == PaymentIntentStatus.EXPIRED
