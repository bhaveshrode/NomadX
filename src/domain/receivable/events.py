from datetime import datetime
from typing import Optional
from ..shared.events.domain_event import DomainEvent
from .value_objects import ReceivableId, CustomerId, InvoiceId, ReceivableReference, CustomerReference, InvoiceReference
from ..organization.value_objects import OrganizationId
from ..invoice.value_objects import Money
from .dunning import DunningStage


class ReceivableCreated(DomainEvent):
    """Event raised when a new Receivable obligation is created."""
    def __init__(
        self,
        receivable_id: ReceivableId,
        organization_id: OrganizationId,
        reference: ReceivableReference,
        invoice_id: InvoiceId,
        invoice_reference: InvoiceReference,
        customer_id: CustomerId,
        customer_reference: CustomerReference,
        original_amount: Money,
        due_date: datetime,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.receivable_id = receivable_id
        self.organization_id = organization_id
        self.reference = reference
        self.invoice_id = invoice_id
        self.invoice_reference = invoice_reference
        self.customer_id = customer_id
        self.customer_reference = customer_reference
        self.original_amount = original_amount
        self.due_date = due_date

    def get_aggregate_id(self) -> str:
        return str(self.receivable_id.value)


class ReceivablePartiallyPaid(DomainEvent):
    """Event raised when a Receivable is partially paid by a payment allocation."""
    def __init__(
        self,
        receivable_id: ReceivableId,
        organization_id: OrganizationId,
        allocated_amount: Money,
        paid_amount: Money,
        outstanding_amount: Money,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.receivable_id = receivable_id
        self.organization_id = organization_id
        self.allocated_amount = allocated_amount
        self.paid_amount = paid_amount
        self.outstanding_amount = outstanding_amount

    def get_aggregate_id(self) -> str:
        return str(self.receivable_id.value)


class ReceivableSettled(DomainEvent):
    """Event raised when a Receivable is fully paid / settled."""
    def __init__(
        self,
        receivable_id: ReceivableId,
        organization_id: OrganizationId,
        allocated_amount: Money,
        paid_amount: Money,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.receivable_id = receivable_id
        self.organization_id = organization_id
        self.allocated_amount = allocated_amount
        self.paid_amount = paid_amount

    def get_aggregate_id(self) -> str:
        return str(self.receivable_id.value)


# Keep ReceivablePaid for backward compatibility in existing code/tests
class ReceivablePaid(ReceivableSettled):
    pass


class ReceivableBecameOverdue(DomainEvent):
    """Event raised when a Receivable becomes overdue."""
    def __init__(
        self,
        receivable_id: ReceivableId,
        organization_id: OrganizationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.receivable_id = receivable_id
        self.organization_id = organization_id

    def get_aggregate_id(self) -> str:
        return str(self.receivable_id.value)


class ReceivableWrittenOff(DomainEvent):
    """Event raised when a Receivable is written off as uncollectible."""
    def __init__(
        self,
        receivable_id: ReceivableId,
        organization_id: OrganizationId,
        write_off_reason: str,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.receivable_id = receivable_id
        self.organization_id = organization_id
        self.write_off_reason = write_off_reason

    def get_aggregate_id(self) -> str:
        return str(self.receivable_id.value)


class ReceivablePaymentAllocated(DomainEvent):
    """Event raised when a payment allocation occurs against a receivable."""
    def __init__(
        self,
        receivable_id: ReceivableId,
        organization_id: OrganizationId,
        payment_id: str,
        allocated_amount: Money,
        allocation_reference: str,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.receivable_id = receivable_id
        self.organization_id = organization_id
        self.payment_id = payment_id
        self.allocated_amount = allocated_amount
        self.allocation_reference = allocation_reference

    def get_aggregate_id(self) -> str:
        return str(self.receivable_id.value)


class ReceivableAgingThresholdReached(DomainEvent):
    """Event raised when a receivable crosses an aging range threshold."""
    def __init__(
        self,
        receivable_id: ReceivableId,
        organization_id: OrganizationId,
        aging_bucket: str,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.receivable_id = receivable_id
        self.organization_id = organization_id
        self.aging_bucket = aging_bucket

    def get_aggregate_id(self) -> str:
        return str(self.receivable_id.value)


class DunningStageChanged(DomainEvent):
    """Event raised when the dunning stage of a receivable is advanced."""
    def __init__(
        self,
        receivable_id: ReceivableId,
        organization_id: OrganizationId,
        previous_stage: DunningStage,
        new_stage: DunningStage,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.receivable_id = receivable_id
        self.organization_id = organization_id
        self.previous_stage = previous_stage
        self.new_stage = new_stage

    def get_aggregate_id(self) -> str:
        return str(self.receivable_id.value)


class ReceivableCollectionAttentionRequired(DomainEvent):
    """Event raised when a Receivable requires collection attention."""
    def __init__(
        self,
        receivable_id: ReceivableId,
        organization_id: OrganizationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.receivable_id = receivable_id
        self.organization_id = organization_id

    def get_aggregate_id(self) -> str:
        return str(self.receivable_id.value)
