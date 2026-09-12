import pytest
from decimal import Decimal
from uuid import uuid4
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.src.infrastructure.blockchain.registry import BlockchainAssetRegistry
from nomadx.src.infrastructure.blockchain.cursor import BlockchainCursorRepository
from nomadx.src.infrastructure.blockchain.clients.fake import FakeBlockchainClient
from nomadx.src.infrastructure.blockchain.adapters.base_adapter import BaseBlockchainPaymentAdapter
from nomadx.src.infrastructure.database.models import BlockchainCursorDbModel

from nomadx.src.application.payment.commands import DetectPaymentCommand


def test_asset_registry_resolution_and_decimal_math():
    registry = BlockchainAssetRegistry()
    
    # 1. Base USDC registration resolution
    resolved = registry.resolve_asset("BASE", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913")
    assert resolved is not None
    symbol, decimals = resolved
    assert symbol == "USDC"
    assert decimals == 6

    # 2. Case insensitivity check
    resolved_checksum = registry.resolve_asset("base", "0x833589FCD6eDb6E08f4c7C32D4f71b54bdA02913")
    assert resolved_checksum is not None
    assert resolved_checksum[0] == "USDC"

    # 3. Decimal conversion check (safe conversion from raw_amount using resolved decimals)
    raw_amount = 5000000000  # 5,000 USDC
    converted = Decimal(raw_amount) / (10 ** decimals)
    assert converted == Decimal("5000.00")
    
    # Check 18 decimals fiat/token (e.g. 1 ETH = 10^18 wei)
    registry.register_asset("BASE", "0xWETH", "WETH", 18)
    res_weth = registry.resolve_asset("base", "0xweth")
    assert res_weth[1] == 18
    raw_weth = 1500000000000000000  # 1.5 WETH
    converted_weth = Decimal(raw_weth) / (10 ** 18)
    assert converted_weth == Decimal("1.5")


@pytest.mark.asyncio
async def test_blockchain_cursor_persistence(db_session: AsyncSession):
    cursor_repo = BlockchainCursorRepository(db_session)
    
    # 1. Get empty cursor
    chk = await cursor_repo.get_checkpoint("BASE")
    assert chk is None

    # 2. Update cursor
    await cursor_repo.update_checkpoint("BASE", 12345)
    await db_session.flush()

    # 3. Retrieve cursor
    chk = await cursor_repo.get_checkpoint("BASE")
    assert chk == 12345

    # Case insensitivity test
    chk_lower = await cursor_repo.get_checkpoint("base")
    assert chk_lower == 12345


@pytest.mark.asyncio
async def test_base_blockchain_payment_adapter_scan_and_normalization():
    registry = BlockchainAssetRegistry()
    usdc_contract = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    
    # Set up Fake RPC Provider
    fake_client = FakeBlockchainClient(current_height=1000)
    fake_client.add_simulated_transfer_log(
        block_number=950,
        tx_hash="0xTxReferenceHash123",
        token_address=usdc_contract,
        sender="0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
        receiver="0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
        raw_amount=250000000  # 250.00 USDC
    )

    adapter = BaseBlockchainPaymentAdapter(fake_client, registry, "BASE")

    # Execute scan
    transfers = await adapter.scan_transfers(from_block=900, to_block=960)
    
    assert len(transfers) == 1
    transfer = transfers[0]
    assert transfer.transaction_reference == "0xTxReferenceHash123"
    assert transfer.network == "BASE"
    assert transfer.token_address.lower() == usdc_contract.lower()
    assert transfer.sender == "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"
    assert transfer.receiver == "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"
    assert transfer.raw_amount == 250000000
    assert transfer.block_number == 950

    # Test converting transfer to application command
    resolved = registry.resolve_asset(transfer.network, transfer.token_address)
    symbol, decimals = resolved
    money_amount = Decimal(transfer.raw_amount) / (10 ** decimals)

    cmd = DetectPaymentCommand(
        payment_id=str(uuid4()),
        payment_intent_id=str(uuid4()),
        observed_amount=money_amount,
        asset_code=symbol,
        asset_type="DIGITAL_ASSET",
        asset_decimals=decimals,
        network_code=transfer.network,
        network_type="BLOCKCHAIN",
        transaction_reference=transfer.transaction_reference,
        sender=transfer.sender,
        receiver=transfer.receiver,
        observed_at=transfer.observed_at,
        organization_id=str(uuid4())
    )

    assert cmd.observed_amount == Decimal("250.00")
    assert cmd.asset_code == "USDC"
    assert cmd.network_code == "BASE"
