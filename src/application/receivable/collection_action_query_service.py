from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from ...domain.organization.value_objects import OrganizationId


class ICollectionActionQueryService(ABC):
    """
    Query service interface defining read-only operations for collection action reporting,
    dead-letter queue viewing, and admin dashboards.
    """

    @abstractmethod
    async def get_failed_actions(
        self,
        organization_id: OrganizationId,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Retrieves a paginated list of failed collection actions."""
        pass

    @abstractmethod
    async def get_dead_letter_actions(
        self,
        organization_id: OrganizationId,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Retrieves a paginated list of dead-lettered collection actions."""
        pass

    @abstractmethod
    async def get_collection_action_metrics(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """Aggregates operational collection engine metrics for dashboard rendering."""
        pass
