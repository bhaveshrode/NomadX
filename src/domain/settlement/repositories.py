from abc import ABC, abstractmethod
from typing import List, Optional

from .settlement import Settlement
from .value_objects import SettlementId, SettlementReference
from ..organization.value_objects import OrganizationId


class ISettlementRepository(ABC):
    """
    Interface for Settlement repository. Enforces bounded-context integrity
    for Settlement persistence and retrieval operations.
    """

    @abstractmethod
    async def get_by_id(self, id: SettlementId) -> Optional[Settlement]:
        """Retrieves a Settlement by its ID."""
        pass

    @abstractmethod
    async def find_by_reference(self, reference: SettlementReference) -> Optional[Settlement]:
        """Retrieves a Settlement by its merchant-facing reference."""
        pass

    @abstractmethod
    async def find_by_payment_id(self, payment_id: str) -> List[Settlement]:
        """Retrieves all Settlements associated with a source payment ID."""
        pass

    @abstractmethod
    async def find_by_organization(self, organization_id: OrganizationId) -> List[Settlement]:
        """Retrieves all Settlements for an organization."""
        pass

    @abstractmethod
    async def find_pending(self) -> List[Settlement]:
        """Retrieves all Settlements currently in PENDING state."""
        pass

    @abstractmethod
    async def save(self, settlement: Settlement) -> None:
        """Saves/persists the Settlement aggregate."""
        pass
