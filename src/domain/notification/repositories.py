from abc import ABC, abstractmethod
from typing import List, Optional

from .notification import Notification
from .value_objects import NotificationId, NotificationReference, NotificationIdempotencyKey, NotificationChannel
from ..organization.value_objects import OrganizationId


class INotificationRepository(ABC):
    """
    Interface/Port for Notification repository.
    Enforces bounded-context integrity for Notification persistence.
    """

    @abstractmethod
    async def get_by_id(self, id: NotificationId) -> Optional[Notification]:
        """Retrieves a Notification by its ID."""
        pass

    @abstractmethod
    async def find_by_reference(self, reference: NotificationReference) -> Optional[Notification]:
        """Retrieves a Notification by its merchant-facing reference."""
        pass

    @abstractmethod
    async def find_by_organization(self, organization_id: OrganizationId) -> List[Notification]:
        """Retrieves all Notifications for an organization."""
        pass

    @abstractmethod
    async def find_by_idempotency_key(
        self,
        organization_id: OrganizationId,
        idempotency_key: NotificationIdempotencyKey
    ) -> Optional[Notification]:
        """Retrieves a Notification by organization and its idempotency key."""
        pass

    @abstractmethod
    async def find_pending(self) -> List[Notification]:
        """Retrieves all Notifications currently in PENDING or RETRYING states."""
        pass

    @abstractmethod
    async def find_by_source_event(self, source_event_id: str, source_event_type: str) -> List[Notification]:
        """Retrieves Notifications by their source event ID and type."""
        pass

    @abstractmethod
    async def save(self, notification: Notification) -> None:
        """Saves/persists the Notification aggregate."""
        pass


class INotificationTemplateRepository(ABC):
    """
    Interface/Port for NotificationTemplate repository.
    Enforces bounded-context integrity for Notification templates.
    """

    @abstractmethod
    async def get_by_id(self, id: any) -> Optional[any]:
        """Retrieves a NotificationTemplate by its ID."""
        pass

    @abstractmethod
    async def get_active_version(
        self,
        organization_id: OrganizationId,
        reference: any
    ) -> Optional[any]:
        """Retrieves the active version of a template for an organization by reference."""
        pass

    @abstractmethod
    async def find_by_reference_and_version(
        self,
        organization_id: OrganizationId,
        reference: any,
        version: any
    ) -> Optional[any]:
        """Retrieves a specific version of a template for an organization."""
        pass

    @abstractmethod
    async def save(self, template: any) -> None:
        """Saves/persists the NotificationTemplate aggregate."""
        pass

    @abstractmethod
    async def delete(self, id: any) -> None:
        """Deletes/deactivates the template with audit protection guards."""
        pass


class INotificationPreferenceRepository(ABC):
    """
    Interface/Port for NotificationPreference repository.
    Enforces bounded-context integrity for Notification preferences.
    """

    @abstractmethod
    async def find_preference(
        self,
        organization_id: OrganizationId,
        event_type: str,
        channel: NotificationChannel
    ) -> Optional[any]:
        """Retrieves a specific NotificationPreference override."""
        pass

    @abstractmethod
    async def save(self, preference: any) -> None:
        """Saves/persists the NotificationPreference aggregate."""
        pass
