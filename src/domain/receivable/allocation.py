from datetime import datetime
from ..shared.value_objects.value_object import ValueObject
from .value_objects import ReceivableId
from ..invoice.value_objects import Money, Currency


class ReceivableAllocation(ValueObject):
    """
    Immutable Value Object representing an allocation of a payment to a receivable.
    """
    def __init__(
        self,
        payment_id: str,
        receivable_id: ReceivableId,
        allocated_amount: Money,
        currency: Currency,
        allocated_at: datetime,
        allocation_reference: str
    ):
        if not payment_id or not isinstance(payment_id, str) or payment_id.strip() == "":
            raise ValueError("Payment identifier must be a valid non-empty string.")
        if not isinstance(receivable_id, ReceivableId):
            raise ValueError("Receivable identifier must be a valid ReceivableId.")
        if not isinstance(allocated_amount, Money):
            raise ValueError("Allocated amount must be a Money object.")
        if not isinstance(currency, Currency):
            raise ValueError("Currency must be a Currency object.")
        if not isinstance(allocated_at, datetime):
            raise ValueError("Allocated timestamp must be a datetime object.")
        if not allocation_reference or not isinstance(allocation_reference, str) or allocation_reference.strip() == "":
            raise ValueError("Allocation reference must be a valid non-empty string.")

        if allocated_amount.amount == 0:
            raise ValueError("Allocated amount must not be zero.")
        if allocated_amount.currency != currency:
            raise ValueError("Allocated amount currency must match allocation currency.")

        super().__init__({
            "payment_id": payment_id,
            "receivable_id": receivable_id,
            "allocated_amount": allocated_amount,
            "currency": currency,
            "allocated_at": allocated_at,
            "allocation_reference": allocation_reference
        })

    @property
    def payment_id(self) -> str:
        return self.props["payment_id"]

    @property
    def receivable_id(self) -> ReceivableId:
        return self.props["receivable_id"]

    @property
    def allocated_amount(self) -> Money:
        return self.props["allocated_amount"]

    @property
    def currency(self) -> Currency:
        return self.props["currency"]

    @property
    def allocated_at(self) -> datetime:
        return self.props["allocated_at"]

    @property
    def allocation_reference(self) -> str:
        return self.props["allocation_reference"]
