from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import Optional

@dataclass(frozen=True)
class SettlementExecutionRequest:
    settlement_id: str
    amount: Decimal
    asset_code: str
    asset_decimals: int
    network: str
    destination_identifier: str
    idempotency_key: str


@dataclass(frozen=True)
class SettlementExecutionResult:
    is_success: bool
    transaction_reference: Optional[str] = None
    network: Optional[str] = None
    submitted_at: Optional[datetime] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    failure_retryable: bool = False
