import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from uuid import uuid4

from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import Money, Currency, CurrencyType
from nomadx.src.domain.invoice import InvoiceId
from nomadx.src.domain.payment import (
    PaymentId,
    PaymentIntentId,
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
from nomadx.src.infrastructure.blockchain.registry import BlockchainAssetRegistry
from nomadx.src.application.payment.verification import (
    PaymentVerificationEvidence,
    PaymentVerificationService,
    ConfirmationPolicy
)


@pytest.fixture
def asset_registry():
    registry = BlockchainAssetRegistry()
    # BASE USDC Contract
    registry.register_asset("BASE", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "USDC", 6)
    return registry


@pytest.fixture
def verification_policy():
    return ConfirmationPolicy({"BASE": 3})


@pytest.fixture
def verification_service(asset_registry, verification_policy):
    return PaymentVerificationService(asset_registry, verification_policy)


@pytest.fixture
def org_id():
    return OrganizationId(str(uuid4()))


@pytest.fixture
def base_payment(org_id):
    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    observed_amount = Money(Decimal("100.00"), currency)
    asset = PaymentAsset("USDC", "DIGITAL_ASSET", 6)
    network = PaymentNetwork("BASE", "BLOCKCHAIN")
    
    return Payment.create(
        id=PaymentId(str(uuid4())),
        organization_id=org_id,
        payment_intent_id=PaymentIntentId(str(uuid4())),
        observed_amount=observed_amount,
        asset=asset,
        network=network,
        transaction_reference="0xTxReference123",
        sender="0xsenderaddress",
        receiver="0xreceiveraddress",
        observed_at=datetime.utcnow()
    ).value


@pytest.fixture
def base_intent(org_id):
    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    requested_amount = Money(Decimal("100.00"), currency)
    asset = PaymentAsset("USDC", "DIGITAL_ASSET", 6)
    destination = PaymentDestination("BLOCKCHAIN_ADDRESS", "0xreceiveraddress", "BASE")
    
    intent = PaymentIntent.create(
        id=PaymentIntentId(str(uuid4())),
        organization_id=org_id,
        invoice_id=InvoiceId(str(uuid4())),
        reference=PaymentReference("REF-XYZ"),
        requested_amount=requested_amount,
        requested_asset=asset,
        destination=destination,
        payment_method=PaymentMethod.DIGITAL_ASSET,
        expires_at=datetime.utcnow() + timedelta(days=1)
    ).value
    intent.activate()
    return intent


def test_verification_policy_success(verification_service, base_payment, base_intent):
    evidence = PaymentVerificationEvidence(
        network="BASE",
        token_contract="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        sender="0xsenderaddress",
        receiver="0xreceiveraddress",
        raw_amount=100000000,  # 100 USDC
        transaction_status=1,
        block_number=950,
        current_block_number=960,  # 11 confirmations
        transaction_reference="0xTxReference123"
    )

    result = verification_service.verify(base_payment, base_intent, evidence)
    assert result.valid is True
    assert len(result.reasons) == 0


def test_verification_policy_network_mismatch(verification_service, base_payment, base_intent):
    evidence = PaymentVerificationEvidence(
        network="ETHEREUM",  # wrong network
        token_contract="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        sender="0xsenderaddress",
        receiver="0xreceiveraddress",
        raw_amount=100000000,
        transaction_status=1,
        block_number=950,
        current_block_number=960,
        transaction_reference="0xTxReference123"
    )

    result = verification_service.verify(base_payment, base_intent, evidence)
    assert result.valid is False
    assert "NETWORK_MISMATCH" in result.reasons


def test_verification_policy_asset_contract_mismatch(verification_service, base_payment, base_intent):
    evidence = PaymentVerificationEvidence(
        network="BASE",
        token_contract="0xWrongContractAddress...",
        sender="0xsenderaddress",
        receiver="0xreceiveraddress",
        raw_amount=100000000,
        transaction_status=1,
        block_number=950,
        current_block_number=960,
        transaction_reference="0xTxReference123"
    )

    result = verification_service.verify(base_payment, base_intent, evidence)
    assert result.valid is False
    assert "UNKNOWN_ASSET" in result.reasons


def test_verification_policy_receiver_mismatch(verification_service, base_payment, base_intent):
    evidence = PaymentVerificationEvidence(
        network="BASE",
        token_contract="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        sender="0xsenderaddress",
        receiver="0xWrongReceiverAddress",
        raw_amount=100000000,
        transaction_status=1,
        block_number=950,
        current_block_number=960,
        transaction_reference="0xTxReference123"
    )

    result = verification_service.verify(base_payment, base_intent, evidence)
    assert result.valid is False
    assert "DESTINATION_MISMATCH" in result.reasons


def test_verification_policy_transaction_failed(verification_service, base_payment, base_intent):
    evidence = PaymentVerificationEvidence(
        network="BASE",
        token_contract="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        sender="0xsenderaddress",
        receiver="0xreceiveraddress",
        raw_amount=100000000,
        transaction_status=0,  # failed EVM transaction
        block_number=950,
        current_block_number=960,
        transaction_reference="0xTxReference123"
    )

    result = verification_service.verify(base_payment, base_intent, evidence)
    assert result.valid is False
    assert "TRANSACTION_FAILED" in result.reasons


def test_verification_policy_insufficient_confirmations(verification_service, base_payment, base_intent):
    evidence = PaymentVerificationEvidence(
        network="BASE",
        token_contract="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        sender="0xsenderaddress",
        receiver="0xreceiveraddress",
        raw_amount=100000000,
        transaction_status=1,
        block_number=950,
        current_block_number=951,  # 2 confirmations (3 required)
        transaction_reference="0xTxReference123"
    )

    result = verification_service.verify(base_payment, base_intent, evidence)
    assert result.valid is False
    assert "INSUFFICIENT_CONFIRMATIONS" in result.reasons


def test_verification_policy_intent_lifecycle_expired(verification_service, base_payment, base_intent):
    base_intent.expire()  # expire intent
    
    evidence = PaymentVerificationEvidence(
        network="BASE",
        token_contract="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        sender="0xsenderaddress",
        receiver="0xreceiveraddress",
        raw_amount=100000000,
        transaction_status=1,
        block_number=950,
        current_block_number=960,
        transaction_reference="0xTxReference123"
    )

    result = verification_service.verify(base_payment, base_intent, evidence)
    assert result.valid is False
    assert "PAYMENT_INTENT_EXPIRED" in result.reasons


def test_verification_policy_intent_lifecycle_cancelled(verification_service, base_payment, base_intent):
    base_intent.cancel()  # cancel intent
    
    evidence = PaymentVerificationEvidence(
        network="BASE",
        token_contract="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        sender="0xsenderaddress",
        receiver="0xreceiveraddress",
        raw_amount=100000000,
        transaction_status=1,
        block_number=950,
        current_block_number=960,
        transaction_reference="0xTxReference123"
    )

    result = verification_service.verify(base_payment, base_intent, evidence)
    assert result.valid is False
    assert "PAYMENT_INTENT_CANCELLED" in result.reasons


def test_verification_policy_amount_tolerances(verification_service, base_payment, base_intent):
    # Partial payment amount (20 USDC instead of 100 USDC)
    evidence_partial = PaymentVerificationEvidence(
        network="BASE",
        token_contract="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        sender="0xsenderaddress",
        receiver="0xreceiveraddress",
        raw_amount=20000000,
        transaction_status=1,
        block_number=950,
        current_block_number=960,
        transaction_reference="0xTxReference123"
    )
    result_partial = verification_service.verify(base_payment, base_intent, evidence_partial)
    assert result_partial.valid is True

    # Overpayment amount (250 USDC instead of 100 USDC)
    evidence_over = PaymentVerificationEvidence(
        network="BASE",
        token_contract="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        sender="0xsenderaddress",
        receiver="0xreceiveraddress",
        raw_amount=250000000,
        transaction_status=1,
        block_number=950,
        current_block_number=960,
        transaction_reference="0xTxReference123"
    )
    result_over = verification_service.verify(base_payment, base_intent, evidence_over)
    assert result_over.valid is True
