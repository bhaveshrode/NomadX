import pytest
from decimal import Decimal
from uuid import uuid4

from nomadx.src.application.settlement.verification import (
    SettlementVerificationService,
    SettlementVerificationEvidence,
    ConfirmationPolicy
)
from nomadx.src.domain.settlement.settlement import Settlement
from nomadx.src.domain.settlement.value_objects import (
    SettlementId,
    SettlementReference,
    SettlementAsset,
    SettlementNetwork,
    SettlementDestination,
    SettlementMethod,
    SettlementSource,
    SettlementIdempotencyKey
)
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import Currency, CurrencyType, Money
from nomadx.src.infrastructure.blockchain.registry import BlockchainAssetRegistry


def _create_test_settlement(
    org_id: str,
    amount: Decimal = Decimal("1000.00"),
    asset_code: str = "USDC",
    network: str = "BASE",
    destination: str = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
) -> Settlement:
    money = Money(amount, Currency(asset_code, CurrencyType.DIGITAL_ASSET))
    dest = SettlementDestination(
        "BLOCKCHAIN_ADDRESS",
        destination,
        SettlementNetwork(network)
    )
    return Settlement.create(
        id=SettlementId(str(uuid4())),
        organization_id=OrganizationId(org_id),
        reference=SettlementReference("SET-VER-123"),
        asset=SettlementAsset(asset_code, "DIGITAL_ASSET", 6),
        network=SettlementNetwork(network),
        amount=money,
        destination=dest,
        method=SettlementMethod.DIGITAL_ASSET,
        source=SettlementSource("PAYMENT", str(uuid4())),
        idempotency_key=SettlementIdempotencyKey("idem-test")
    ).value


def test_settlement_verification_service_success():
    registry = BlockchainAssetRegistry()  # prepopulated with BASE / 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913 -> USDC (6)
    policy = ConfirmationPolicy()
    service = SettlementVerificationService(registry, policy)

    org_id = str(uuid4())
    settlement = _create_test_settlement(org_id)

    # Valid evidence matching settlement exactly with 3 confirmations on BASE
    evidence = SettlementVerificationEvidence(
        transaction_reference="0xTxHashValid",
        network="BASE",
        asset="USDC",
        contract_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        destination="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        observed_amount=Decimal("1000.00"),
        transaction_status=1,
        transaction_block=100,
        current_block=102,  # 102 - 100 + 1 = 3 confirmations
        confirmations=3
    )

    result = service.verify(settlement, evidence)
    assert result.valid is True
    assert len(result.reasons) == 0
    assert result.network_verified is True
    assert result.asset_verified is True
    assert result.contract_verified is True
    assert result.destination_verified is True
    assert result.amount_verified is True
    assert result.transaction_verified is True
    assert result.finality_verified is True


def test_settlement_verification_failures():
    registry = BlockchainAssetRegistry()
    policy = ConfirmationPolicy()
    service = SettlementVerificationService(registry, policy)

    org_id = str(uuid4())
    settlement = _create_test_settlement(org_id)

    # 1. Wrong Network
    ev_wrong_network = SettlementVerificationEvidence(
        transaction_reference="0xTxHash",
        network="ETHEREUM",
        asset="USDC",
        contract_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        destination="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        observed_amount=Decimal("1000.00"),
        transaction_status=1,
        transaction_block=100,
        current_block=102,
        confirmations=3
    )
    res = service.verify(settlement, ev_wrong_network)
    assert res.valid is False
    assert "NETWORK_MISMATCH" in res.reasons

    # 2. Wrong Asset Contract Address (unknown contract)
    ev_unknown_contract = SettlementVerificationEvidence(
        transaction_reference="0xTxHash",
        network="BASE",
        asset="USDT",
        contract_address="0xUnknownContractAddressHere",
        destination="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        observed_amount=Decimal("1000.00"),
        transaction_status=1,
        transaction_block=100,
        current_block=102,
        confirmations=3
    )
    res = service.verify(settlement, ev_unknown_contract)
    assert res.valid is False
    assert "UNKNOWN_ASSET_CONTRACT" in res.reasons

    # 3. Contract mismatch (known contract but wrong one for this symbol)
    registry.register_asset("BASE", "0xWrongContract", "EURT", 6)
    ev_wrong_contract = SettlementVerificationEvidence(
        transaction_reference="0xTxHash",
        network="BASE",
        asset="EURT",
        contract_address="0xWrongContract",
        destination="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        observed_amount=Decimal("1000.00"),
        transaction_status=1,
        transaction_block=100,
        current_block=102,
        confirmations=3
    )
    res = service.verify(settlement, ev_wrong_contract)
    assert res.valid is False
    assert "ASSET_MISMATCH" in res.reasons or "CONTRACT_MISMATCH" in res.reasons

    # 4. Wrong Destination Address
    ev_wrong_dest = SettlementVerificationEvidence(
        transaction_reference="0xTxHash",
        network="BASE",
        asset="USDC",
        contract_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        destination="0xWrongDestinationWalletAddressHere",
        observed_amount=Decimal("1000.00"),
        transaction_status=1,
        transaction_block=100,
        current_block=102,
        confirmations=3
    )
    res = service.verify(settlement, ev_wrong_dest)
    assert res.valid is False
    assert "DESTINATION_MISMATCH" in res.reasons

    # 5. Wrong Amount
    ev_wrong_amount = SettlementVerificationEvidence(
        transaction_reference="0xTxHash",
        network="BASE",
        asset="USDC",
        contract_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        destination="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        observed_amount=Decimal("500.00"),  # Expected 1000.00
        transaction_status=1,
        transaction_block=100,
        current_block=102,
        confirmations=3
    )
    res = service.verify(settlement, ev_wrong_amount)
    assert res.valid is False
    assert "AMOUNT_MISMATCH" in res.reasons

    # 6. Reverted/Failed transaction status
    ev_failed_status = SettlementVerificationEvidence(
        transaction_reference="0xTxHash",
        network="BASE",
        asset="USDC",
        contract_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        destination="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        observed_amount=Decimal("1000.00"),
        transaction_status=0,  # 0 = Reverted
        transaction_block=100,
        current_block=102,
        confirmations=3
    )
    res = service.verify(settlement, ev_failed_status)
    assert res.valid is False
    assert "TRANSACTION_FAILED" in res.reasons

    # 7. Insufficient confirmations
    ev_low_confirmations = SettlementVerificationEvidence(
        transaction_reference="0xTxHash",
        network="BASE",
        asset="USDC",
        contract_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        destination="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        observed_amount=Decimal("1000.00"),
        transaction_status=1,
        transaction_block=100,
        current_block=100,  # only 1 block confirmation (100 - 100 + 1 = 1)
        confirmations=1
    )
    res = service.verify(settlement, ev_low_confirmations)
    assert res.valid is False
    assert "INSUFFICIENT_CONFIRMATIONS" in res.reasons
