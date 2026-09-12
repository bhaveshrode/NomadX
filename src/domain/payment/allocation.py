from datetime import datetime
from typing import Dict, Any

from ..shared.entities.aggregate_root import AggregateRoot
from ..shared.result.result import Result, ResultError
from ..shared.clock.clock import Clock
from ..organization.value_objects import OrganizationId
from ..invoice.value_objects import InvoiceId, Money
from .value_objects import PaymentId, PaymentAllocationId
from .events import PaymentReconciled


class PaymentAllocation(AggregateRoot[PaymentAllocationId]):
    """
    Aggregate root representing a financial allocation from a confirmed Payment to an Invoice.
    """
    def __init__(self, id: PaymentAllocationId, props: Dict[str, Any]):
        super().__init__(id)
        self._props = props

    @property
    def payment_id(self) -> PaymentId:
        return self._props["payment_id"]

    @property
    def invoice_id(self) -> InvoiceId:
        return self._props["invoice_id"]

    @property
    def organization_id(self) -> OrganizationId:
        return self._props["organization_id"]

    @property
    def allocated_amount(self) -> Money:
        return self._props["allocated_amount"]

    @property
    def status(self) -> str:
        return self._props["status"]

    @property
    def created_at(self) -> datetime:
        return self._props["created_at"]

    @property
    def updated_at(self) -> datetime:
        return self._props["updated_at"]

    @classmethod
    def create(
        cls,
        id: PaymentAllocationId,
        payment_id: PaymentId,
        invoice_id: InvoiceId,
        organization_id: OrganizationId,
        allocated_amount: Money,
        status: str = "ALLOCATED"
    ) -> Result["PaymentAllocation"]:
        """
        Factory method to construct a new PaymentAllocation aggregate.
        """
        if None in (id, payment_id, invoice_id, organization_id, allocated_amount):
            return Result.fail(ResultError.validation("All fields are mandatory and cannot be null."))

        if allocated_amount.amount <= 0:
            return Result.fail(ResultError.validation("Allocated amount must be positive."))

        now = Clock.now()

        props = {
            "payment_id": payment_id,
            "invoice_id": invoice_id,
            "organization_id": organization_id,
            "allocated_amount": allocated_amount,
            "status": status,
            "created_at": now,
            "updated_at": now
        }

        instance = cls(id, props)
        # Emit domain event
        instance._add_domain_event(PaymentReconciled(payment_id, invoice_id, allocated_amount, now))
        return Result.ok(instance)
