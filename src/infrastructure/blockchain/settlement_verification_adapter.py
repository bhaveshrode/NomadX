from typing import Optional
from decimal import Decimal
from datetime import datetime, timezone

from ...application.settlement.verification import SettlementVerificationEvidence
from ...infrastructure.settlement.interfaces import ISettlementVerificationAdapter
from .registry import BlockchainAssetRegistry


class BlockchainSettlementVerificationAdapter(ISettlementVerificationAdapter):
    """
    Adapter fetching transaction receipt details from the network client
    and parsing standard Transfer event logs for verification.
    """
    def __init__(self, client_provider, registry: BlockchainAssetRegistry) -> None:
        self.client_provider = client_provider
        self.registry = registry

    async def fetch_evidence(self, network: str, transaction_reference: str) -> Optional[SettlementVerificationEvidence]:
        try:
            client = self.client_provider.get_client(network)
        except KeyError:
            return None

        # Fetch transaction receipt details
        receipt = await client.get_transaction_receipt(transaction_reference)
        if not receipt:
            return None

        # Parse logs to extract ERC-20 Transfer log details
        token_contract = None
        recipient = None
        amount_raw = 0

        for log in receipt.get("logs", []):
            topics = log.get("topics", [])
            # Standard ERC-20 Transfer topic signature: Transfer(address,address,uint256)
            if len(topics) >= 3 and topics[0] == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef":
                token_contract = log.get("address")
                
                # recipient address is in topics[2] (padded to 32 bytes)
                recipient_raw = topics[2]
                if len(recipient_raw) == 66:
                    recipient = "0x" + recipient_raw[26:]
                else:
                    recipient = recipient_raw

                # raw amount is in log data
                data_str = log.get("data", "0x0")
                try:
                    amount_raw = int(data_str, 16)
                except ValueError:
                    amount_raw = 0
                break

        if not token_contract or not recipient:
            return None

        # Resolve asset details from BlockchainAssetRegistry to determine symbol/decimals
        asset_details = self.registry.resolve_asset(network, token_contract)
        if not asset_details:
            return None

        symbol, decimals = asset_details
        observed_amount = Decimal(amount_raw) / Decimal(10 ** decimals)

        # Get block confirmations
        tx_block = receipt.get("blockNumber", 0)
        try:
            current_block = await client.get_latest_block_number()
        except Exception:
            # Fallback if get_latest_block_number fails temporarily
            current_block = tx_block

        confirmations = max(0, current_block - tx_block + 1)

        return SettlementVerificationEvidence(
            transaction_reference=transaction_reference,
            network=network.upper(),
            asset=symbol,
            contract_address=token_contract,
            destination=recipient,
            observed_amount=observed_amount,
            transaction_status=receipt.get("status", 0),
            transaction_block=tx_block,
            current_block=current_block,
            confirmations=confirmations
        )
