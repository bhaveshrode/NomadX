from typing import List, Dict, Any, Optional
from ..interfaces import IBlockchainClient


class FakeBlockchainClient(IBlockchainClient):
    """
    Simulates blockchain RPC operations, returning pre-programmed block height
    and log transfer events to simplify integration testing.
    """
    def __init__(self, current_height: int = 1000) -> None:
        self._height = current_height
        self._logs: List[Dict[str, Any]] = []
        self._receipts: Dict[str, Dict[str, Any]] = {}

    def set_block_height(self, height: int) -> None:
        self._height = height

    def add_simulated_transfer_log(
        self,
        block_number: int,
        tx_hash: str,
        token_address: str,
        sender: str,
        receiver: str,
        raw_amount: int
    ) -> None:
        """Pipes a mock ERC-20 transfer log to simulate query responses."""
        # Pad addresses to 32 bytes (64 characters)
        padded_sender = "0x" + sender.lower().replace("0x", "").rjust(64, "0")
        padded_receiver = "0x" + receiver.lower().replace("0x", "").rjust(64, "0")
        
        # Pad amount to 32 bytes
        padded_amount = "0x" + hex(raw_amount).replace("0x", "").rjust(64, "0")

        log = {
            "blockNumber": hex(block_number),
            "transactionHash": tx_hash,
            "address": token_address.lower(),
            "topics": [
                "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef", # Transfer(address,address,uint256)
                padded_sender,
                padded_receiver
            ],
            "data": padded_amount
        }
        self._logs.append(log)
        # Also auto-populate a successful receipt
        self._receipts[tx_hash.lower()] = {
            "status": "0x1",
            "blockNumber": hex(block_number),
            "transactionHash": tx_hash
        }

    def add_simulated_receipt(self, tx_hash: str, status: str, block_number: int) -> None:
        """Explicitly adds or overrides a mock receipt (e.g. status='0x0' to test failure)."""
        self._receipts[tx_hash.lower()] = {
            "status": status,
            "blockNumber": hex(block_number),
            "transactionHash": tx_hash
        }

    async def get_latest_block_number(self) -> int:
        return self._height

    async def get_transaction_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        if tx_hash.lower() not in self._receipts:
            self.add_simulated_transfer_log(
                block_number=950,
                tx_hash=tx_hash,
                token_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", # USDC
                sender="0xSenderAddress",
                receiver="0x53d6118c7c9ad072a445e98218175d9e5ec3e742",
                raw_amount=1000000000 # 1000.00 USDC
            )
        return self._receipts.get(tx_hash.lower())

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str,
        topics: List[str]
    ) -> List[Dict[str, Any]]:
        filtered = []
        for log in self._logs:
            log_block = int(log["blockNumber"], 16)
            if not (from_block <= log_block <= to_block):
                continue
            if address and log["address"].lower() != address.lower():
                continue
            if topics:
                # Match topics checklist
                match = True
                for i, topic in enumerate(topics):
                    if i < len(log["topics"]) and topic.lower() != log["topics"][i].lower():
                        match = False
                        break
                if not match:
                    continue
            filtered.append(log)
        return filtered
