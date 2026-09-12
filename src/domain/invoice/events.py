from datetime import datetime
from typing import Optional
from ..shared.events.domain_event import DomainEvent
from .value_objects import InvoiceId, InvoiceNumber
from ..organization.value_objects import OrganizationId


class InvoiceCreated(DomainEvent):
    """Event raised when a new Invoice is created in DRAFT state."""
    def __init__(
        self,
        invoice_id: InvoiceId,
        organization_id: OrganizationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.invoice_id = invoice_id
        self.organization_id = organization_id

    def get_aggregate_id(self) -> str:
        return str(self.invoice_id.value)


class InvoiceIssued(DomainEvent):
    """Event raised when an Invoice is transition to ISSUED state."""
    def __init__(
        self,
        invoice_id: InvoiceId,
        invoice_number: InvoiceNumber,
        organization_id: OrganizationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.invoice_id = invoice_id
        self.invoice_number = invoice_number
        self.organization_id = organization_id

    def get_aggregate_id(self) -> str:
        return str(self.invoice_id.value)


class InvoiceVoided(DomainEvent):
    """Event raised when an Invoice is transition to VOIDED state."""
    def __init__(
        self,
        invoice_id: InvoiceId,
        organization_id: OrganizationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.invoice_id = invoice_id
        self.organization_id = organization_id

    def get_aggregate_id(self) -> str:
        return str(self.invoice_id.value)
