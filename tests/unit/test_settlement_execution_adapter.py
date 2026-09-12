import pytest
from decimal import Decimal
from uuid import uuid4

from nomadx.src.infrastructure.settlement.fake import FakeSettlementExecutionAdapter
from nomadx.src.infrastructure.blockchain.settlement_adapter import BaseBlockchainSettlementAdapter
from nomadx.src.infrastructure.blockchain.registry import BlockchainAssetRegistry
from nomadx.src.infrastructure.settlement.models import SettlementExecutionRequest


@pytest.mark.asyncio
async def test_fake_settlement_execution_adapter():
    adapter = FakeSettlementExecutionAdapter()
    
    # 1. Successful execution simulation
    req1 = SettlementExecutionRequest(
        settlement_id=str(uuid4()),
        amount=Decimal("1500.00"),
        asset_code="USDC",
        asset_decimals=6,
        network="BASE",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        idempotency_key="idem-fake-success"
    )
    
    res1 = await adapter.execute(req1)
    assert res1.is_success is True
    assert res1.transaction_reference.startswith("0xFakeTx_")
    assert res1.network == "BASE"
    assert res1.submitted_at is not None
    assert res1.failure_code is None

    # 2. Retryable failure simulation
    req2 = SettlementExecutionRequest(
        settlement_id=str(uuid4()),
        amount=Decimal("1000.00"),
        asset_code="USDC",
        asset_decimals=6,
        network="BASE",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        idempotency_key="idem-fake-retryable"
    )
    adapter.set_behavior("idem-fake-retryable", "RETRYABLE_FAILURE")
    
    res2 = await adapter.execute(req2)
    assert res2.is_success is False
    assert res2.failure_code == "NETWORK_UNAVAILABLE"
    assert res2.failure_retryable is True

    # 3. Permanent failure simulation
    req3 = SettlementExecutionRequest(
        settlement_id=str(uuid4()),
        amount=Decimal("1000.00"),
        asset_code="USDC",
        asset_decimals=6,
        network="BASE",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        idempotency_key="idem-fake-permanent"
    )
    adapter.set_behavior("idem-fake-permanent", "PERMANENT_FAILURE")
    
    res3 = await adapter.execute(req3)
    assert res3.is_success is False
    assert res3.failure_code == "DESTINATION_INVALID"
    assert res3.failure_retryable is False

    # 4. Idempotency test (returns same result for duplicate idempotency key)
    res1_dup = await adapter.execute(req1)
    assert res1_dup.is_success is True
    assert res1_dup.transaction_reference == res1.transaction_reference


@pytest.mark.asyncio
async def test_blockchain_settlement_adapter_validations():
    registry = BlockchainAssetRegistry() # prepopulated with USDC/BASE
    adapter = BaseBlockchainSettlementAdapter(registry)

    # 1. Valid USDC BASE execution
    req_valid = SettlementExecutionRequest(
        settlement_id=str(uuid4()),
        amount=Decimal("2500.00"),
        asset_code="USDC",
        asset_decimals=6,
        network="BASE",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        idempotency_key="idem-bc-valid"
    )
    
    res_valid = await adapter.execute(req_valid)
    assert res_valid.is_success is True
    assert res_valid.transaction_reference.startswith("0xBlockchainTx_")
    assert res_valid.network == "BASE"

    # 2. Unknown asset / network rejected
    req_unknown = SettlementExecutionRequest(
        settlement_id=str(uuid4()),
        amount=Decimal("2500.00"),
        asset_code="EURT",
        asset_decimals=6,
        network="BASE",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        idempotency_key="idem-bc-unknown"
    )
    
    res_unknown = await adapter.execute(req_unknown)
    assert res_unknown.is_success is False
    assert "Unsupported asset symbol" in res_unknown.failure_message
    assert res_unknown.failure_retryable is False

    # 3. Decimals mismatch rejected
    req_decimals = SettlementExecutionRequest(
        settlement_id=str(uuid4()),
        amount=Decimal("2500.00"),
        asset_code="USDC",
        asset_decimals=18, # Mainnet USDC registry has 6
        network="BASE",
        destination_identifier="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        idempotency_key="idem-bc-dec"
    )
    
    res_dec = await adapter.execute(req_decimals)
    assert res_dec.is_success is False
    assert "decimals mismatch" in res_dec.failure_message
    assert res_dec.failure_retryable is False

    # 4. Invalid destination blockchain address format rejected
    req_dest_invalid = SettlementExecutionRequest(
        settlement_id=str(uuid4()),
        amount=Decimal("2500.00"),
        asset_code="USDC",
        asset_decimals=6,
        network="BASE",
        destination_identifier="0xInvalidDestAddressShort",
        idempotency_key="idem-bc-dest"
    )
    
    res_dest = await adapter.execute(req_dest_invalid)
    assert res_dest.is_success is False
    assert res_dest.failure_code == "DESTINATION_INVALID"
    assert res_dest.failure_retryable is False
