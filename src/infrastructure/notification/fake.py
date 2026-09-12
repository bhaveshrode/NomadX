from typing import Dict, Optional
from .interfaces import INotificationDeliveryAdapter
from .models import NotificationDeliveryRequest, NotificationDeliveryResult


class FakeEmailDeliveryAdapter(INotificationDeliveryAdapter):
    """
    Fake execution adapter simulating email notifications dispatching.
    Tracks execution history in-memory for testing crash recovery and idempotency.
    """
    def __init__(self) -> None:
        self._delivery_history: Dict[str, NotificationDeliveryResult] = {}
        self._behavior_overrides: Dict[str, str] = {}

    def set_behavior(self, key: str, behavior: str) -> None:
        """Sets expected adapter behavior for a given idempotency key or notification ID."""
        self._behavior_overrides[key] = behavior

    async def deliver(self, request: NotificationDeliveryRequest) -> NotificationDeliveryResult:
        # Check idempotency first (DUPLICATE_REQUEST / CRASH RECOVERY)
        if request.idempotency_key in self._delivery_history:
            return self._delivery_history[request.idempotency_key]

        # Recipient email formatting check
        if "@" not in request.recipient:
            return NotificationDeliveryResult(
                is_success=False,
                failure_code="INVALID_RECIPIENT",
                failure_message="Invalid email address format",
                failure_retryable=False
            )

        behavior = (
            self._behavior_overrides.get(request.idempotency_key) or
            self._behavior_overrides.get(request.notification_id)
        )

        if behavior == "TEMPORARY_FAILURE":
            res = NotificationDeliveryResult(
                is_success=False,
                failure_code="PROVIDER_RATE_LIMIT",
                failure_message="Fake email provider rate limit reached.",
                failure_retryable=True
            )
        elif behavior == "TIMEOUT":
            res = NotificationDeliveryResult(
                is_success=False,
                failure_code="EMAIL_PROVIDER_TIMEOUT",
                failure_message="Fake email provider request timed out.",
                failure_retryable=True
            )
        elif behavior == "PERMANENT_FAILURE":
            res = NotificationDeliveryResult(
                is_success=False,
                failure_code="PROVIDER_REJECTED",
                failure_message="Fake email provider rejected request permanently.",
                failure_retryable=False
            )
        else:
            # Default SUCCESS
            res = NotificationDeliveryResult(
                is_success=True,
                provider_reference=f"email-ref-{request.notification_id[:8]}"
            )

        self._delivery_history[request.idempotency_key] = res
        return res


class FakeWebhookDeliveryAdapter(INotificationDeliveryAdapter):
    """
    Fake execution adapter simulating webhook notification dispatches.
    Tracks execution history in-memory for testing crash recovery and idempotency.
    """
    def __init__(self) -> None:
        self._delivery_history: Dict[str, NotificationDeliveryResult] = {}
        self._behavior_overrides: Dict[str, str] = {}

    def set_behavior(self, key: str, behavior: str) -> None:
        """Sets expected adapter behavior for a given idempotency key or notification ID."""
        self._behavior_overrides[key] = behavior

    async def deliver(self, request: NotificationDeliveryRequest) -> NotificationDeliveryResult:
        # Check idempotency first
        if request.idempotency_key in self._delivery_history:
            return self._delivery_history[request.idempotency_key]

        # Webhook destination https check
        if not request.recipient.startswith("https://"):
            return NotificationDeliveryResult(
                is_success=False,
                failure_code="INVALID_RECIPIENT",
                failure_message="Webhook recipient must start with https://.",
                failure_retryable=False
            )

        behavior = (
            self._behavior_overrides.get(request.idempotency_key) or
            self._behavior_overrides.get(request.notification_id)
        )

        if behavior == "TEMPORARY_FAILURE":
            res = NotificationDeliveryResult(
                is_success=False,
                failure_code="WEBHOOK_TIMEOUT",
                failure_message="Fake webhook destination temporary 503 error.",
                failure_retryable=True
            )
        elif behavior == "PERMANENT_FAILURE":
            res = NotificationDeliveryResult(
                is_success=False,
                failure_code="PROVIDER_REJECTED",
                failure_message="Fake webhook destination returned permanent 400 bad request.",
                failure_retryable=False
            )
        else:
            # Default SUCCESS
            res = NotificationDeliveryResult(
                is_success=True,
                provider_reference=f"webhook-ref-{request.notification_id[:8]}"
            )

        self._delivery_history[request.idempotency_key] = res
        return res


class NotificationDeliveryAdapter(INotificationDeliveryAdapter):
    """
    Composite/Router implementation that inspects the request's channel
    and routes it to the corresponding adapter.
    """
    def __init__(
        self,
        email_adapter: INotificationDeliveryAdapter,
        webhook_adapter: INotificationDeliveryAdapter
    ) -> None:
        self._email_adapter = email_adapter
        self._webhook_adapter = webhook_adapter

    async def deliver(self, request: NotificationDeliveryRequest) -> NotificationDeliveryResult:
        if request.channel == "EMAIL":
            return await self._email_adapter.deliver(request)
        elif request.channel == "WEBHOOK":
            return await self._webhook_adapter.deliver(request)
        else:
            return NotificationDeliveryResult(
                is_success=False,
                failure_code="UNKNOWN_ERROR",
                failure_message=f"Unsupported notification channel: {request.channel}",
                failure_retryable=False
            )
