from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import List, Optional

@dataclass(frozen=True)
class CreateSettlementCommand:
    settlement_id: str
    organization_id: str
    reference: str
    merchant_reference: str
    start_date: datetime
    end_date: datetime
    currency_code: str
    idempotency_key: str

@dataclass(frozen=True)
class OpenSettlementCommand:
    settlement_id: str
    organization_id: str

@dataclass(frozen=True)
class AddSettlementEntryCommand:
    settlement_id: str
    organization_id: str
    entry_reference: str
    source_transaction_reference: str
    source_context_type: str
    gross_amount: Decimal
    fees_amount: Decimal
    adjustments_amount: Decimal
    net_amount: Decimal
    currency_code: str

@dataclass(frozen=True)
class AddSettlementAdjustmentCommand:
    settlement_id: str
    organization_id: str
    adjustment_id: str
    type: str
    description: str
    amount: Decimal
    currency_code: str

@dataclass(frozen=True)
class BeginReconciliationCommand:
    settlement_id: str
    organization_id: str

@dataclass(frozen=True)
class ReconcileSettlementCommand:
    settlement_id: str
    organization_id: str
    status: str
    discrepancies: List[dict]
    verified_at: datetime
    description: str

@dataclass(frozen=True)
class RecordSettlementDiscrepancyCommand:
    settlement_id: str
    organization_id: str
    entry_reference: str
    discrepancy_type: str
    expected_amount: Decimal
    actual_amount: Decimal
    description: str

@dataclass(frozen=True)
class ResolveSettlementDiscrepancyCommand:
    settlement_id: str
    organization_id: str
    entry_reference: str

@dataclass(frozen=True)
class CompleteSettlementCommand:
    settlement_id: str
    organization_id: str

@dataclass(frozen=True)
class CancelSettlementCommand:
    settlement_id: str
    organization_id: str
