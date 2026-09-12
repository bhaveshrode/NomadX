from dataclasses import dataclass
from typing import Dict, Any


@dataclass(frozen=True)
class CreateNotificationCommand:
    notification_id: str
    organization_id: str
    reference: str
    notification_type: str
    recipient_destination: str
    channel: str
    subject: str
    template_id: str
    payload: Dict[str, Any]
    priority: str
    idempotency_key: str
    source_event_id: str
    source_event_type: str


@dataclass(frozen=True)
class StartNotificationProcessingCommand:
    notification_id: str
    organization_id: str


@dataclass(frozen=True)
class MarkNotificationDeliveredCommand:
    notification_id: str
    organization_id: str


@dataclass(frozen=True)
class MarkNotificationFailedCommand:
    notification_id: str
    organization_id: str
    failure_code: str
    failure_message: str
    failure_retryable: bool


@dataclass(frozen=True)
class RetryNotificationCommand:
    notification_id: str
    organization_id: str


@dataclass(frozen=True)
class CancelNotificationCommand:
    notification_id: str
    organization_id: str
