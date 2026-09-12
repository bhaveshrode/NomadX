from abc import ABC, abstractmethod
from typing import Optional
from ..shared.repositories.repository import IRepository
from .organization import Organization
from .value_objects import OrganizationId, OrganizationSlug

class IOrganizationRepository(IRepository[Organization, OrganizationId], ABC):
    """
    Domain Repository Contract for Organization Aggregate.
    """
    @abstractmethod
    async def find_by_slug(self, slug: OrganizationSlug) -> Optional[Organization]:
        """
        Retrieves an organization by its unique slug.
        Returns None if not found.
        """
        pass
