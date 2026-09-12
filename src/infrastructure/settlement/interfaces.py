from abc import ABC, abstractmethod
from typing import Optional
from .models import SettlementExecutionRequest, SettlementExecutionResult
from ...application.settlement.verification import SettlementVerificationEvidence

class ISettlementExecutionAdapter(ABC):
    @abstractmethod
    async def execute(self, request: SettlementExecutionRequest) -> SettlementExecutionResult:
        """
        Executes the settlement externally.
        Must be idempotent based on request.idempotency_key.
        """
        pass


class ITransactionSigner(ABC):
    @abstractmethod
    async def sign_and_send(self, network: str, transaction_payload: dict) -> str:
        """
        Signs and broadcasts transaction, returning transaction hash.
        """
        pass


class ISettlementVerificationAdapter(ABC):
    @abstractmethod
    async def fetch_evidence(self, network: str, transaction_reference: str) -> Optional[SettlementVerificationEvidence]:
        """
        Fetches normalized evidence from the blockchain network for verification.
        Returns None if transaction reference does not exist on the network.
        """
        pass
