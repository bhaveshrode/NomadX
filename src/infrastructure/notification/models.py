from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class NotificationDeliveryRequest:
    notification_id: str
    organization_id: str
    channel: str
    recipient: str
    notification_type: str
    subject: str
    payload: dict
    idempotency_key: str


@dataclass(frozen=True)
class NotificationDeliveryResult:
    is_success: bool
    provider_reference: Optional[str] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    failure_retryable: bool = False
