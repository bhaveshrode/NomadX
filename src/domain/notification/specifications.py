from typing import Any

from ..shared.specifications.specification import Specification


class NotificationCanBeProcessed(Specification["Notification"]):
    """Specification verifying if a Notification can transition to PROCESSING."""
    def is_satisfied_by(self, candidate: "Notification") -> bool:
        from .notification import NotificationStatus
        return candidate.status in (NotificationStatus.PENDING, NotificationStatus.RETRYING)


class NotificationCanBeDelivered(Specification["Notification"]):
    """Specification verifying if a Notification can transition to DELIVERED."""
    def is_satisfied_by(self, candidate: "Notification") -> bool:
        from .notification import NotificationStatus
        return candidate.status == NotificationStatus.PROCESSING


class NotificationCanBeRetried(Specification["Notification"]):
    """Specification verifying if a Notification can transition back to RETRYING from FAILED."""
    def is_satisfied_by(self, candidate: "Notification") -> bool:
        from .notification import NotificationStatus
        if candidate.status != NotificationStatus.FAILED:
            return False
        return candidate.failure is not None and candidate.failure.retryable


class NotificationCanBeCancelled(Specification["Notification"]):
    """Specification verifying if a Notification can transition to CANCELLED."""
    def is_satisfied_by(self, candidate: "Notification") -> bool:
        from .notification import NotificationStatus
        return candidate.status == NotificationStatus.PENDING


class NotificationIsPending(Specification["Notification"]):
    """Specification checking if a Notification is PENDING."""
    def is_satisfied_by(self, candidate: "Notification") -> bool:
        from .notification import NotificationStatus
        return candidate.status == NotificationStatus.PENDING


class NotificationIsRetryable(Specification["Notification"]):
    """Specification checking if a Notification failure is retryable."""
    def is_satisfied_by(self, candidate: "Notification") -> bool:
        return candidate.failure is not None and candidate.failure.retryable
