from abc import ABC, abstractmethod
from typing import Optional, List
from ..shared.repositories.repository import IRepository
from ..organization.value_objects import OrganizationId
from .value_objects import InvoiceId, InvoiceNumber


# Forward declare Invoice to avoid circular import issues
class Invoice:
    pass


class IInvoiceRepository(IRepository["Invoice", InvoiceId], ABC):
    """
    Domain Repository Contract for Invoice Aggregate.
    """
    @abstractmethod
    async def find_by_invoice_number(
        self,
        organization_id: OrganizationId,
        invoice_number: InvoiceNumber
    ) -> Optional["Invoice"]:
        """
        Retrieves an invoice by its unique invoice number within an organization.
        Returns None if not found.
        """
        pass

    @abstractmethod
    async def find_by_organization(
        self,
        organization_id: OrganizationId
    ) -> List["Invoice"]:
        """
        Retrieves all invoices belonging to a specific organization.
        """
        pass
