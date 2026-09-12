from datetime import datetime, timezone
from ..settlement.interfaces import ISettlementExecutionAdapter
from ..settlement.models import SettlementExecutionRequest, SettlementExecutionResult
from .registry import BlockchainAssetRegistry

class BaseBlockchainSettlementAdapter(ISettlementExecutionAdapter):
    """
    Adapter bridging normalized Settlement requests into concrete blockchain operations.
    Performs asset, network, decimals, and destination validations.
    """
    def __init__(self, registry: BlockchainAssetRegistry) -> None:
        self.registry = registry
        self._execution_history = {}

    async def execute(self, request: SettlementExecutionRequest) -> SettlementExecutionResult:
        # Check idempotency first (Crash Recovery boundary)
        if request.idempotency_key in self._execution_history:
            return self._execution_history[request.idempotency_key]

        # 1. Validate network and asset details against BlockchainAssetRegistry
        found = False
        resolved_contract = None
        resolved_decimals = None

        for (net, contract), (sym, dec) in self.registry._registry.items():
            if net == request.network.upper() and sym == request.asset_code.upper():
                resolved_contract = contract
                resolved_decimals = dec
                found = True
                break

        if not found:
            return SettlementExecutionResult(
                is_success=False,
                failure_code="PROVIDER_ERROR",
                failure_message=f"Unsupported asset symbol '{request.asset_code}' on network '{request.network}'.",
                failure_retryable=False
            )

        if resolved_decimals != request.asset_decimals:
            return SettlementExecutionResult(
                is_success=False,
                failure_code="PROVIDER_ERROR",
                failure_message=f"Asset decimals mismatch. Registry expects {resolved_decimals}, got {request.asset_decimals}.",
                failure_retryable=False
            )

        # 2. Validate destination blockchain address (0x prefix, 42 characters)
        dest = request.destination_identifier.strip()
        if not dest.startswith("0x") or len(dest) != 42:
            return SettlementExecutionResult(
                is_success=False,
                failure_code="DESTINATION_INVALID",
                failure_message="Destination address must be a valid 42-character hex address starting with '0x'.",
                failure_retryable=False
            )

        # 3. Simulate signing/broadcast to external network and return transaction reference
        tx_ref = f"0xBlockchainTx_{request.settlement_id[:8]}"
        res = SettlementExecutionResult(
            is_success=True,
            transaction_reference=tx_ref,
            network=request.network.upper(),
            submitted_at=datetime.now(timezone.utc)
        )

        self._execution_history[request.idempotency_key] = res
        return res
