from datetime import datetime, timezone
from typing import Dict, Optional
from .interfaces import ISettlementExecutionAdapter, ISettlementVerificationAdapter
from .models import SettlementExecutionRequest, SettlementExecutionResult
from ...application.settlement.verification import SettlementVerificationEvidence

class FakeSettlementExecutionAdapter(ISettlementExecutionAdapter):
    """
    Fake execution adapter simulating external payment rails.
    Tracks execution history in-memory for testing crash recovery and idempotency.
    """
    def __init__(self) -> None:
        self._execution_history: Dict[str, SettlementExecutionResult] = {}
        self._behavior_overrides: Dict[str, str] = {}

    def set_behavior(self, key: str, behavior: str) -> None:
        """Sets expected adapter behavior for a given idempotency key or settlement ID."""
        self._behavior_overrides[key] = behavior

    async def execute(self, request: SettlementExecutionRequest) -> SettlementExecutionResult:
        # Check idempotency first (DUPLICATE_REQUEST / CRASH RECOVERY)
        if request.idempotency_key in self._execution_history:
            return self._execution_history[request.idempotency_key]

        behavior = (
            self._behavior_overrides.get(request.idempotency_key) or
            self._behavior_overrides.get(request.settlement_id)
        )

        if behavior == "RETRYABLE_FAILURE":
            res = SettlementExecutionResult(
                is_success=False,
                failure_code="NETWORK_UNAVAILABLE",
                failure_message="Fake RPC client rate limit reached.",
                failure_retryable=True
            )
        elif behavior == "PERMANENT_FAILURE":
            res = SettlementExecutionResult(
                is_success=False,
                failure_code="DESTINATION_INVALID",
                failure_message="Fake address format invalid.",
                failure_retryable=False
            )
        else:
            # Default SUCCESS
            res = SettlementExecutionResult(
                is_success=True,
                transaction_reference=f"0xFakeTx_{request.settlement_id[:8]}",
                network=request.network,
                submitted_at=datetime.now(timezone.utc)
            )

        self._execution_history[request.idempotency_key] = res
        return res


class FakeSettlementVerificationAdapter(ISettlementVerificationAdapter):
    """
    Fake verification adapter simulating external transaction observation.
    """
    def __init__(self) -> None:
        self._evidences: Dict[str, SettlementVerificationEvidence] = {}

    def set_evidence(self, transaction_reference: str, evidence: SettlementVerificationEvidence) -> None:
        """Mocks on-chain transaction facts for verification testing."""
        self._evidences[transaction_reference] = evidence

    async def fetch_evidence(self, network: str, transaction_reference: str) -> Optional[SettlementVerificationEvidence]:
        return self._evidences.get(transaction_reference)
