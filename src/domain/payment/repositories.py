from abc import ABC, abstractmethod
from typing import Optional
from .value_objects import (
    PaymentIntentId,
    PaymentId,
    PaymentReference,
    PaymentNetwork
)
from .payment_intent import PaymentIntent
from .payment import Payment


class IPaymentIntentRepository(ABC):
    """
    Contract for persistence and retrieval of PaymentIntent aggregates.
    """
    @abstractmethod
    async def save(self, payment_intent: PaymentIntent) -> None:
        """Saves or updates a PaymentIntent aggregate."""
        pass

    @abstractmethod
    async def find_by_id(self, id: PaymentIntentId) -> Optional[PaymentIntent]:
        """Retrieves a PaymentIntent by its UniqueEntityID."""
        pass

    @abstractmethod
    async def find_by_reference(self, reference: PaymentReference) -> Optional[PaymentIntent]:
        """Retrieves a PaymentIntent by its merchant reference code."""
        pass


class IPaymentRepository(ABC):
    """
    Contract for persistence and retrieval of Payment aggregates.
    """
    @abstractmethod
    async def save(self, payment: Payment) -> None:
        """Saves or updates a Payment aggregate."""
        pass

    @abstractmethod
    async def find_by_id(self, id: PaymentId) -> Optional[Payment]:
        """Retrieves a Payment by its UniqueEntityID."""
        pass

    @abstractmethod
    async def find_by_transaction_reference(
        self,
        network: PaymentNetwork,
        transaction_reference: str
    ) -> Optional[Payment]:
        """
        Retrieves a Payment by its network and transaction reference (hash).
        Used to prevent duplicate payment processing.
        """
        pass


from typing import List
from ..invoice.value_objects import InvoiceId
from .value_objects import PaymentAllocationId
from .allocation import PaymentAllocation

class IPaymentAllocationRepository(ABC):
    """
    Contract for persistence and retrieval of PaymentAllocation aggregates.
    """
    @abstractmethod
    async def save(self, allocation: PaymentAllocation) -> None:
        """Saves or updates a PaymentAllocation aggregate."""
        pass

    @abstractmethod
    async def find_by_id(self, id: PaymentAllocationId) -> Optional[PaymentAllocation]:
        """Retrieves a PaymentAllocation by its UniqueEntityID."""
        pass

    @abstractmethod
    async def find_by_payment_id(self, payment_id: PaymentId) -> List[PaymentAllocation]:
        """Retrieves allocations funded by the specified payment."""
        pass

    @abstractmethod
    async def find_by_invoice_id(self, invoice_id: InvoiceId) -> List[PaymentAllocation]:
        """Retrieves allocations made to the specified invoice."""
        pass
