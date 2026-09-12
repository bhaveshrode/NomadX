from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class CreateReceivableCommand:
    receivable_id: str
    organization_id: str
    invoice_id: str


@dataclass(frozen=True)
class ApplyPaymentAllocationCommand:
    organization_id: str
    receivable_id: str
    payment_id: str
    allocation_id: str
    allocated_amount: Decimal
    currency_code: str


@dataclass(frozen=True)
class AllocatePaymentCommand:
    organization_id: str
    receivable_id: str
    payment_id: str
    allocated_amount: Decimal
    currency_code: str
    allocation_reference: str


@dataclass(frozen=True)
class MarkReceivableOverdueCommand:
    organization_id: str
    receivable_id: str


@dataclass(frozen=True)
class SettleReceivableCommand:
    organization_id: str
    receivable_id: str


@dataclass(frozen=True)
class WriteOffReceivableCommand:
    organization_id: str
    receivable_id: str
    reason: str


@dataclass(frozen=True)
class AdvanceDunningStageCommand:
    organization_id: str
    receivable_id: str
    next_stage: str
    next_attempt_at: Optional[datetime] = None


@dataclass(frozen=True)
class UpdateReceivableAgingCommand:
    organization_id: str
    receivable_id: str
