from datetime import datetime
from typing import Optional

from ..shared.events.domain_event import DomainEvent
from .value_objects import NotificationId, NotificationFailure
from ..organization.value_objects import OrganizationId


class NotificationCreated(DomainEvent):
    """Event emitted when a notification is first created."""
    def __init__(
        self,
        notification_id: NotificationId,
        organization_id: OrganizationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.notification_id = notification_id
        self.organization_id = organization_id

    def get_aggregate_id(self) -> str:
        return str(self.notification_id.value)


class NotificationProcessingStarted(DomainEvent):
    """Event emitted when delivery processing starts for a notification."""
    def __init__(
        self,
        notification_id: NotificationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.notification_id = notification_id

    def get_aggregate_id(self) -> str:
        return str(self.notification_id.value)


class NotificationDelivered(DomainEvent):
    """Event emitted when a notification is successfully delivered."""
    def __init__(
        self,
        notification_id: NotificationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.notification_id = notification_id

    def get_aggregate_id(self) -> str:
        return str(self.notification_id.value)


class NotificationDeliveryFailed(DomainEvent):
    """Event emitted when a notification delivery attempt fails."""
    def __init__(
        self,
        notification_id: NotificationId,
        failure: NotificationFailure,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.notification_id = notification_id
        self.failure = failure

    def get_aggregate_id(self) -> str:
        return str(self.notification_id.value)


class NotificationRetrying(DomainEvent):
    """Event emitted when a retryable failed notification schedules a new attempt."""
    def __init__(
        self,
        notification_id: NotificationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.notification_id = notification_id

    def get_aggregate_id(self) -> str:
        return str(self.notification_id.value)


class NotificationCancelled(DomainEvent):
    """Event emitted when a pending notification is cancelled."""
    def __init__(
        self,
        notification_id: NotificationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.notification_id = notification_id

    def get_aggregate_id(self) -> str:
        return str(self.notification_id.value)


class NotificationDeadLettered(DomainEvent):
    """Event emitted when a notification retry exhaustion transitions it to dead-letter state."""
    def __init__(
        self,
        notification_id: NotificationId,
        reason: str,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.notification_id = notification_id
        self.reason = reason

    def get_aggregate_id(self) -> str:
        return str(self.notification_id.value)


class NotificationReplayed(DomainEvent):
    """Event emitted when a notification is replayed/redrived by operator intervention."""
    def __init__(
        self,
        notification_id: NotificationId,
        occurred_on: Optional[datetime] = None
    ):
        super().__init__(occurred_on)
        self.notification_id = notification_id

    def get_aggregate_id(self) -> str:
        return str(self.notification_id.value)
