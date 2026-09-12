from dataclasses import dataclass, field
from typing import List, Dict, Optional
from ...domain.payment.payment import Payment
from ...domain.payment.payment_intent import PaymentIntent, PaymentIntentStatus
from ...infrastructure.blockchain.registry import BlockchainAssetRegistry
from ...infrastructure.blockchain.interfaces import IBlockchainClient


@dataclass(frozen=True)
class PaymentVerificationEvidence:
    """
    On-chain transaction facts fetched from the network provider client.
    """
    network: str
    token_contract: str
    sender: str
    receiver: str
    raw_amount: int
    transaction_status: int  # 1 = Success, 0 = Failed
    block_number: int
    current_block_number: int
    transaction_reference: str


@dataclass(frozen=True)
class VerificationResult:
    """
    Structured response from the payment verification policy engine.
    """
    valid: bool
    reasons: List[str] = field(default_factory=list)
    network_verified: bool = False
    asset_verified: bool = False
    destination_verified: bool = False
    transaction_verified: bool = False
    amount_verified: bool = False
    finality_verified: bool = False


class ConfirmationPolicy:
    """
    Registry for finality block counts required per network.
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


class PaymentVerificationService:
    """
    Policy engine validating whether on-chain transaction logs constitute valid payments.
    """
    def __init__(
        self,
        registry: BlockchainAssetRegistry,
        policy: ConfirmationPolicy
    ) -> None:
        self._registry = registry
        self._policy = policy

    def verify(
        self,
        payment: Payment,
        intent: Optional[PaymentIntent],
        evidence: PaymentVerificationEvidence
    ) -> VerificationResult:
        reasons = []
        
        # 1. Verify Network
        network_ok = payment.network.code.upper() == evidence.network.upper()
        if intent and intent.destination.network:
            if intent.destination.network.upper() != evidence.network.upper():
                network_ok = False
        
        if not network_ok:
            reasons.append("NETWORK_MISMATCH")

        # 2. Verify Asset and Contract
        asset_details = self._registry.resolve_asset(evidence.network, evidence.token_contract)
        asset_ok = False
        if not asset_details:
            reasons.append("UNKNOWN_ASSET")
        else:
            symbol, decimals = asset_details
            if symbol.upper() != payment.asset.code.upper():
                reasons.append("ASSET_MISMATCH")
            elif contract_lower := evidence.token_contract.lower():
                if intent and intent.requested_asset.code.upper() != symbol.upper():
                    reasons.append("ASSET_MISMATCH")
                else:
                    asset_ok = True
            else:
                reasons.append("TOKEN_CONTRACT_MISMATCH")

        # 3. Verify Destination (Receiver Address)
        dest_ok = payment.receiver.lower() == evidence.receiver.lower()
        if intent and intent.destination.identifier.lower() != evidence.receiver.lower():
            dest_ok = False

        if not dest_ok:
            reasons.append("DESTINATION_MISMATCH")

        # 4. Verify Transaction Success Status
        tx_ok = evidence.transaction_status == 1
        if not tx_ok:
            reasons.append("TRANSACTION_FAILED")

        # 5. Verify Amount (is technically valid as long as positive; downstream reconciles matches)
        amount_ok = evidence.raw_amount > 0

        # 6. Verify Block Finality / Confirmation count
        req_confirmations = self._policy.get_required_confirmations(evidence.network)
        confirmations = max(0, evidence.current_block_number - evidence.block_number + 1)
        finality_ok = confirmations >= req_confirmations
        if not finality_ok:
            reasons.append("INSUFFICIENT_CONFIRMATIONS")

        # 7. Verify Intent State
        if intent:
            if intent.status == PaymentIntentStatus.CANCELLED:
                reasons.append("PAYMENT_INTENT_CANCELLED")
            elif intent.status == PaymentIntentStatus.EXPIRED:
                reasons.append("PAYMENT_INTENT_EXPIRED")

        valid = len(reasons) == 0
        return VerificationResult(
            valid=valid,
            reasons=reasons,
            network_verified=network_ok,
            asset_verified=asset_ok,
            destination_verified=dest_ok,
            transaction_verified=tx_ok,
            amount_verified=amount_ok,
            finality_verified=finality_ok
        )


class BlockchainClientProvider:
    """
    Application-level provider coordinating RPC client access per network code.
    """
    def __init__(self) -> None:
        self._providers: Dict[str, IBlockchainClient] = {}

    def register_client(self, network: str, client: IBlockchainClient) -> None:
        self._providers[network.upper()] = client

    def get_client(self, network: str) -> IBlockchainClient:
        net = network.upper()
        if net not in self._providers:
            raise KeyError(f"No blockchain client registered for network '{network}'")
        return self._providers[net]
