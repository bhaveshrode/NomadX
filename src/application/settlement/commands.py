from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class CreateSettlementCommand:
    settlement_id: str
    organization_id: str
    payment_id: str
    amount: Decimal
    asset_code: str
    asset_type: str
    asset_decimals: int
    network: str
    destination_type: str
    destination_identifier: str
    method: str
    reference: str
    idempotency_key: str


@dataclass(frozen=True)
class StartSettlementProcessingCommand:
    settlement_id: str
    organization_id: str


@dataclass(frozen=True)
class SubmitSettlementCommand:
    settlement_id: str
    organization_id: str
    transaction_reference: str


@dataclass(frozen=True)
class ConfirmSettlementCommand:
    settlement_id: str
    organization_id: str


@dataclass(frozen=True)
class CompleteSettlementCommand:
    settlement_id: str
    organization_id: str


@dataclass(frozen=True)
class FailSettlementCommand:
    settlement_id: str
    organization_id: str
    failure_code: str
    failure_message: str
    failure_retryable: bool


@dataclass(frozen=True)
class RetrySettlementCommand:
    settlement_id: str
    organization_id: str


@dataclass(frozen=True)
class CancelSettlementCommand:
    settlement_id: str
    organization_id: str
