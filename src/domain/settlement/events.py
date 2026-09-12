from datetime import datetime
from typing import Optional
from ..shared.events.domain_event import DomainEvent
from .value_objects import SettlementId, SettlementFailure
from ..organization.value_objects import OrganizationId


class SettlementCreated(DomainEvent):
    """Event raised when a new Settlement is created."""
    def __init__(
        self,
        settlement_id: SettlementId,
        organization_id: OrganizationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id
        self.organization_id = organization_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementProcessingStarted(DomainEvent):
    """Event raised when settlement begins processing."""
    def __init__(
        self,
        settlement_id: SettlementId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementSubmitted(DomainEvent):
    """Event raised when settlement is submitted to external network."""
    def __init__(
        self,
        settlement_id: SettlementId,
        transaction_reference: str,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id
        self.transaction_reference = transaction_reference

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementConfirmed(DomainEvent):
    """Event raised when settlement transaction is confirmed on external network."""
    def __init__(
        self,
        settlement_id: SettlementId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementCompleted(DomainEvent):
    """Event raised when settlement completes successfully."""
    def __init__(
        self,
        settlement_id: SettlementId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementFailed(DomainEvent):
    """Event raised when settlement fails."""
    def __init__(
        self,
        settlement_id: SettlementId,
        failure: SettlementFailure,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id
        self.failure = failure

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementRetrying(DomainEvent):
    """Event raised when a retryable failed settlement transitions back to retrying."""
    def __init__(
        self,
        settlement_id: SettlementId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)


class SettlementCancelled(DomainEvent):
    """Event raised when a pending settlement is cancelled."""
    def __init__(
        self,
        settlement_id: SettlementId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.settlement_id = settlement_id

    def get_aggregate_id(self) -> str:
        return str(self.settlement_id.value)
