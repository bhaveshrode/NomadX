from datetime import datetime
from typing import Optional
from ..shared.events.domain_event import DomainEvent
from .value_objects import PaymentIntentId, PaymentId
from ..organization.value_objects import OrganizationId


# Payment Intent Events

class PaymentIntentCreated(DomainEvent):
    """Event raised when a new PaymentIntent is created."""
    def __init__(
        self,
        payment_intent_id: PaymentIntentId,
        organization_id: OrganizationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.payment_intent_id = payment_intent_id
        self.organization_id = organization_id

    def get_aggregate_id(self) -> str:
        return str(self.payment_intent_id.value)


class PaymentIntentActivated(DomainEvent):
    """Event raised when a PaymentIntent is activated."""
    def __init__(
        self,
        payment_intent_id: PaymentIntentId,
        organization_id: OrganizationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.payment_intent_id = payment_intent_id
        self.organization_id = organization_id

    def get_aggregate_id(self) -> str:
        return str(self.payment_intent_id.value)


class PaymentIntentExpired(DomainEvent):
    """Event raised when a PaymentIntent expires."""
    def __init__(
        self,
        payment_intent_id: PaymentIntentId,
        organization_id: OrganizationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.payment_intent_id = payment_intent_id
        self.organization_id = organization_id

    def get_aggregate_id(self) -> str:
        return str(self.payment_intent_id.value)


class PaymentIntentCancelled(DomainEvent):
    """Event raised when a PaymentIntent is cancelled."""
    def __init__(
        self,
        payment_intent_id: PaymentIntentId,
        organization_id: OrganizationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.payment_intent_id = payment_intent_id
        self.organization_id = organization_id

    def get_aggregate_id(self) -> str:
        return str(self.payment_intent_id.value)


# Payment Events

class PaymentDetected(DomainEvent):
    """Event raised when a payment is first detected."""
    def __init__(
        self,
        payment_id: PaymentId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.payment_id = payment_id

    def get_aggregate_id(self) -> str:
        return str(self.payment_id.value)


class PaymentVerificationStarted(DomainEvent):
    """Event raised when a payment enters pending verification status."""
    def __init__(
        self,
        payment_id: PaymentId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.payment_id = payment_id

    def get_aggregate_id(self) -> str:
        return str(self.payment_id.value)


class PaymentConfirmed(DomainEvent):
    """Event raised when a payment is successfully verified/confirmed."""
    def __init__(
        self,
        payment_id: PaymentId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.payment_id = payment_id

    def get_aggregate_id(self) -> str:
        return str(self.payment_id.value)


class PaymentRejected(DomainEvent):
    """Event raised when a payment fails verification/is rejected."""
    def __init__(
        self,
        payment_id: PaymentId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.payment_id = payment_id

    def get_aggregate_id(self) -> str:
        return str(self.payment_id.value)


from ..invoice.value_objects import InvoiceId
from ..invoice.value_objects import Money

class PaymentReconciled(DomainEvent):
    """Event raised when a payment is reconciled/allocated to an invoice."""
    def __init__(
        self,
        payment_id: PaymentId,
        invoice_id: InvoiceId,
        allocated_amount: Money,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.payment_id = payment_id
        self.invoice_id = invoice_id
        self.allocated_amount = allocated_amount

    def get_aggregate_id(self) -> str:
        return str(self.payment_id.value)


class PaymentReconciliationException(DomainEvent):
    """Event raised when reconciliation encounters a late, invalid, or mismatched payment."""
    def __init__(
        self,
        payment_id: PaymentId,
        invoice_id: Optional[InvoiceId],
        reason: str,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.payment_id = payment_id
        self.invoice_id = invoice_id
        self.reason = reason

    def get_aggregate_id(self) -> str:
        return str(self.payment_id.value)
