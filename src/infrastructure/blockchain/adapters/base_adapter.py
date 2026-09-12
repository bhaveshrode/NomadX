from typing import List
from datetime import datetime
from ..interfaces import IBlockchainClient, IBlockchainPaymentAdapter
from ..models import ObservedTokenTransfer
from ..registry import BlockchainAssetRegistry


class BaseBlockchainPaymentAdapter(IBlockchainPaymentAdapter):
    """
    Decodes raw JSON-RPC logs into normalized, network-neutral ObservedTokenTransfer entities.
    """
    def __init__(self, client: IBlockchainClient, registry: BlockchainAssetRegistry, network: str) -> None:
        self._client = client
        self._registry = registry
        self._network = network

    async def scan_transfers(self, from_block: int, to_block: int) -> List[ObservedTokenTransfer]:
        """Queries raw ERC-20 transfer logs and reconstructs neutral transfer items."""
        transfers = []
        
        # Collect token contract addresses registered for this network
        registered_contracts = []
        for (net, addr), val in self._registry._registry.items():
            if net.upper() == self._network.upper():
                registered_contracts.append(addr)

        # Standard Transfer topic signature
        transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

        for contract in registered_contracts:
            logs = await self._client.get_logs(
                from_block=from_block,
                to_block=to_block,
                address=contract,
                topics=[transfer_topic]
            )

            for log in logs:
                # Ensure topics count has signature + sender + receiver
                if len(log["topics"]) < 3:
                    continue

                sender = self._decode_address(log["topics"][1])
                receiver = self._decode_address(log["topics"][2])
                
                # Convert data payload hex to integer
                raw_amount = int(log["data"], 16)
                
                block_num = int(log["blockNumber"], 16)
                tx_hash = log["transactionHash"]

                transfers.append(
                    ObservedTokenTransfer(
                        transaction_reference=tx_hash,
                        network=self._network,
                        token_address=contract,
                        sender=sender,
                        receiver=receiver,
                        raw_amount=raw_amount,
                        block_number=block_num,
                        observed_at=datetime.utcnow()
                    )
                )
        return transfers

    def _decode_address(self, topic: str) -> str:
        """Extracts standard lowercased address from 32-byte padded topic."""
        clean = topic.replace("0x", "")
        return "0x" + clean[-40:].lower()
