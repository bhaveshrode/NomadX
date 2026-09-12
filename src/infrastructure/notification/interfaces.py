from abc import ABC, abstractmethod
from .models import NotificationDeliveryRequest, NotificationDeliveryResult


class INotificationDeliveryAdapter(ABC):
    @abstractmethod
    async def deliver(self, request: NotificationDeliveryRequest) -> NotificationDeliveryResult:
        """
        Dispatches a notification to its respective external destination provider.
        Must be idempotent based on request.idempotency_key.
        """
        pass
