from abc import ABC, abstractmethod
from typing import List, Dict, Any
from ...domain.organization.value_objects import OrganizationId


class INotificationQueryService(ABC):
    """
    Query service interface defining read-only notification reporting,
    summary count aggregations, and delivery statistics.
    """

    @abstractmethod
    async def get_delivery_attempts(
        self,
        organization_id: OrganizationId,
        notification_id: str
    ) -> List[Dict[str, Any]]:
        """Retrieves delivery attempts logged for a specific notification."""
        pass

    @abstractmethod
    async def get_notification_summary(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """Retrieves count breakdown of notifications by status for an organization."""
        pass

    @abstractmethod
    async def get_delivery_stats(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        """Retrieves delivery success/failure counts breakdown by channel."""
        pass

    @abstractmethod
    async def get_failed_notifications(
        self,
        organization_id: OrganizationId
    ) -> List[Dict[str, Any]]:
        """Retrieves failed notifications for an organization."""
        pass

    @abstractmethod
    async def get_dead_lettered_notifications(
        self,
        organization_id: OrganizationId
    ) -> List[Dict[str, Any]]:
        """Retrieves dead-lettered notifications for an organization."""
        pass

    @abstractmethod
    async def get_provider_health_states(self) -> List[Dict[str, Any]]:
        """Retrieves health states and consecutive failures of all providers."""
        pass
