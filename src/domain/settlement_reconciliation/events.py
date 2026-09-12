from datetime import datetime
from typing import Optional

from ..shared.events.domain_event import DomainEvent
from .value_objects import SettlementId, MerchantReference, SettlementLineReference

class SettlementCreated(DomainEvent):
    """Event triggered when a new Settlement is created in DRAFT state."""
    def __init__(self, settlement_id: SettlementId, merchant_reference: MerchantReference, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id
        self.merchant_reference = merchant_reference

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)

class SettlementOpened(DomainEvent):
    """Event triggered when a Settlement transitions to OPEN state."""
    def __init__(self, settlement_id: SettlementId, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)

class SettlementEntryAdded(DomainEvent):
    """Event triggered when a new SettlementEntry is added."""
    def __init__(self, settlement_id: SettlementId, entry_reference: SettlementLineReference, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id
        self.entry_reference = entry_reference

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)

class SettlementReconciliationStarted(DomainEvent):
    """Event triggered when reconciliation processing begins."""
    def __init__(self, settlement_id: SettlementId, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)

class SettlementReconciled(DomainEvent):
    """Event triggered when a Settlement is reconciled successfully."""
    def __init__(self, settlement_id: SettlementId, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)

class SettlementDiscrepancyDetected(DomainEvent):
    """Event triggered when a discrepancy is detected during reconciliation."""
    def __init__(self, settlement_id: SettlementId, discrepancy_count: int, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id
        self.discrepancy_count = discrepancy_count

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)

class SettlementCompleted(DomainEvent):
    """Event triggered when a Settlement is successfully completed."""
    def __init__(self, settlement_id: SettlementId, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)

class SettlementCancelled(DomainEvent):
    """Event triggered when a Settlement is cancelled."""
    def __init__(self, settlement_id: SettlementId, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)

class SettlementAdjustmentApplied(DomainEvent):
    """Event triggered when a financial adjustment is applied to the Settlement."""
    def __init__(self, settlement_id: SettlementId, adjustment_id: str, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id
        self.adjustment_id = adjustment_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementProcessingRetried(DomainEvent):
    """Event triggered when a failed/stuck processing execution is retried."""
    def __init__(self, settlement_id: SettlementId, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementProcessingReplayed(DomainEvent):
    """Event triggered when a fresh retry/replay attempt is scheduled."""
    def __init__(self, settlement_id: SettlementId, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementProcessingCancelled(DomainEvent):
    """Event triggered when settlement processing is manually cancelled."""
    def __init__(self, settlement_id: SettlementId, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementProcessingDeadLettered(DomainEvent):
    """Event triggered when max retries are exhausted and settlement is DLQ'd."""
    def __init__(self, settlement_id: SettlementId, last_error: str, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id
        self.last_error = last_error

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementProcessingRecovered(DomainEvent):
    """Event triggered when a stuck processing job is automatically recovered."""
    def __init__(self, settlement_id: SettlementId, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementProcessingPaused(DomainEvent):
    """Event triggered when automated processing is paused."""
    def __init__(self, settlement_id: SettlementId, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementProcessingResumed(DomainEvent):
    """Event triggered when automated processing is resumed."""
    def __init__(self, settlement_id: SettlementId, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class ReconciliationRetryScheduled(DomainEvent):
    """Event triggered when a transient error schedules a retry."""
    def __init__(self, settlement_id: SettlementId, next_retry_at: datetime, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id
        self.next_retry_at = next_retry_at

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class ReconciliationDiscrepancyResolved(DomainEvent):
    """Event triggered when a discrepancy is resolved."""
    def __init__(self, settlement_id: SettlementId, entry_reference: SettlementLineReference, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id
        self.entry_reference = entry_reference

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementProcessingFailed(DomainEvent):
    """Event triggered when processing fails permanently."""
    def __init__(self, settlement_id: SettlementId, error: str, occurred_on: Optional[datetime] = None):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id
        self.error = error

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)
