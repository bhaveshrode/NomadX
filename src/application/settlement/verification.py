from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Dict, Optional

from ...domain.settlement.settlement import Settlement
from ...infrastructure.blockchain.registry import BlockchainAssetRegistry


@dataclass(frozen=True)
class SettlementVerificationEvidence:
    """
    On-chain transaction facts retrieved from the payment rail for verification.
    """
    transaction_reference: str
    network: str
    asset: str
    contract_address: str
    destination: str
    observed_amount: Decimal
    transaction_status: int  # 1 = Success, 0 = Reverted/Failed
    transaction_block: int
    current_block: int
    confirmations: int


@dataclass(frozen=True)
class SettlementVerificationResult:
    """
    Structured outcome of the verification policy evaluation.
    """
    valid: bool
    reasons: List[str] = field(default_factory=list)
    network_verified: bool = False
    asset_verified: bool = False
    contract_verified: bool = False
    destination_verified: bool = False
    amount_verified: bool = False
    transaction_verified: bool = False
    finality_verified: bool = False


class ConfirmationPolicy:
    """
    Policy mapping blockchain network codes to required block confirmation depths.
    """
    def __init__(self, overrides: Optional[Dict[str, int]] = None) -> None:
        self._policies = {
            "BASE": 3,
            "BASE_TESTNET": 1
        }
        if overrides:
            self._policies.update(overrides)

    def get_required_confirmations(self, network: str) -> int:
        return self._policies.get(network.upper(), 3)


class SettlementVerificationService:
    """
    Policy engine validating whether external payment rail evidence satisfies settlement constraints.
    """
    def __init__(self, registry: BlockchainAssetRegistry, policy: ConfirmationPolicy) -> None:
        self._registry = registry
        self._policy = policy

    def verify(self, settlement: Settlement, evidence: SettlementVerificationEvidence) -> SettlementVerificationResult:
        reasons = []

        # 1. Verify Network match
        network_ok = settlement.network.value.upper() == evidence.network.upper()
        if not network_ok:
            reasons.append("NETWORK_MISMATCH")

        # 2. Verify Asset symbol and Contract address using BlockchainAssetRegistry
        asset_details = self._registry.resolve_asset(evidence.network, evidence.contract_address)
        asset_ok = False
        contract_ok = False

        if not asset_details:
            reasons.append("UNKNOWN_ASSET_CONTRACT")
        else:
            symbol, decimals = asset_details
            if symbol.upper() != settlement.asset.code.upper():
                reasons.append("ASSET_MISMATCH")
            else:
                asset_ok = True
                
                # Check contract address resolves to the expected registered one for the network & symbol
                expected_contract = None
                for (net, contract), (sym, dec) in self._registry._registry.items():
                    if net == settlement.network.value.upper() and sym == settlement.asset.code.upper():
                        expected_contract = contract
                        break

                if expected_contract and expected_contract.lower() == evidence.contract_address.lower():
                    contract_ok = True
                else:
                    reasons.append("CONTRACT_MISMATCH")

        if not asset_ok and "UNKNOWN_ASSET_CONTRACT" not in reasons and "ASSET_MISMATCH" not in reasons:
            reasons.append("ASSET_MISMATCH")
        if not contract_ok and "CONTRACT_MISMATCH" not in reasons:
            reasons.append("CONTRACT_MISMATCH")

        # 3. Verify Destination recipient address
        dest_ok = settlement.destination.identifier.lower() == evidence.destination.lower()
        if not dest_ok:
            reasons.append("DESTINATION_MISMATCH")

        # 4. Verify Exact Amount
        amount_ok = settlement.amount.amount == evidence.observed_amount
        if not amount_ok:
            reasons.append("AMOUNT_MISMATCH")

        # 5. Verify Transaction status (1 = Success)
        tx_ok = evidence.transaction_status == 1
        if not tx_ok:
            reasons.append("TRANSACTION_FAILED")

        # 6. Verify Block Finality depth
        req_confirmations = self._policy.get_required_confirmations(evidence.network)
        finality_ok = evidence.confirmations >= req_confirmations
        if not finality_ok:
            reasons.append("INSUFFICIENT_CONFIRMATIONS")

        valid = len(reasons) == 0
        return SettlementVerificationResult(
            valid=valid,
            reasons=reasons,
            network_verified=network_ok,
            asset_verified=asset_ok,
            contract_verified=contract_ok,
            destination_verified=dest_ok,
            amount_verified=amount_ok,
            transaction_verified=tx_ok,
            finality_verified=finality_ok
        )
