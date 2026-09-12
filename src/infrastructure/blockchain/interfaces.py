from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from .models import ObservedTokenTransfer


class IBlockchainClient(ABC):
    """
    Interface/Port representing interactions with the raw blockchain client.
    """
    @abstractmethod
    async def get_latest_block_number(self) -> int:
        """Returns the current block height of the network."""
        pass

    @abstractmethod
    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str,
        topics: List[str]
    ) -> List[Dict[str, Any]]:
        """Queries logs for the specified block range, contract address, and topic filters."""
        pass

    @abstractmethod
    async def get_transaction_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        """Fetches the receipt details for the specified transaction hash."""
        pass


class IBlockchainPaymentAdapter(ABC):
    """
    Interface/Port for scanning and normalizing raw events into network-neutral DTOs.
    """
    @abstractmethod
    async def scan_transfers(
        self,
        from_block: int,
        to_block: int
    ) -> List[ObservedTokenTransfer]:
        """Scans the block range and decodes supported token transfers."""
        pass
