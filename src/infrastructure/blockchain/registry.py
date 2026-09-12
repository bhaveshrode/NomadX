from typing import Dict, Tuple, Optional


class BlockchainAssetRegistry:
    """
    Registry holding blockchain token contract details.
    Maps (network, token_address) -> (symbol, decimals) case-insensitively.
    """
    def __init__(self) -> None:
        # Prepopulate Base USDC configuration
        self._registry: Dict[Tuple[str, str], Tuple[str, int]] = {
            # Base USDC Contract Mainnet
            ("BASE", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"): ("USDC", 6),
            # Base USDC Contract Sepolia/Testnet (for potential testing configs)
            ("BASE_TESTNET", "0x036cbd53842c5426634e7929541ec2318f3dcf7e"): ("USDC", 6),
        }

    def register_asset(self, network: str, contract_address: str, symbol: str, decimals: int) -> None:
        """Dynamically registers new token configuration."""
        key = (network.upper(), contract_address.lower())
        self._registry[key] = (symbol, decimals)

    def resolve_asset(self, network: str, contract_address: str) -> Optional[Tuple[str, int]]:
        """Resolves symbol and decimals for the token contract."""
        key = (network.upper(), contract_address.lower())
        return self._registry.get(key)
