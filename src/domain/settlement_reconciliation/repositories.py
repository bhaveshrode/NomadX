from abc import ABC, abstractmethod
from typing import List, Optional

from .settlement import Settlement
from .value_objects import SettlementId, SettlementReference
from ..organization.value_objects import OrganizationId

class ISettlementRepository(ABC):
    """
    Interface for the Settlement & Reconciliation aggregate repository.
    Enforces tenant-scoped boundaries and async persistence.
    """

    @abstractmethod
    async def get_by_id(self, id: SettlementId) -> Optional[Settlement]:
        """Retrieves a Settlement aggregate by its ID."""
        pass

    @abstractmethod
    async def find_by_reference(self, reference: SettlementReference) -> Optional[Settlement]:
        """Retrieves a Settlement aggregate by its unique reference."""
        pass

    @abstractmethod
    async def find_by_organization(self, organization_id: OrganizationId) -> List[Settlement]:
        """Retrieves all Settlements belonging to an organization/merchant."""
        pass

    @abstractmethod
    async def save(self, settlement: Settlement) -> None:
        """Persists the Settlement aggregate root, committing updates and domain events."""
        pass
