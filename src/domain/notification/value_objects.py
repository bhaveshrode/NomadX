import re
from enum import Enum
from typing import Dict, Any

from ..shared.entities.identifier import UniqueEntityID
from ..shared.value_objects.value_object import ValueObject


class NotificationId(UniqueEntityID):
    """Strongly typed identifier representing a Notification's unique identity."""
    pass


class NotificationReference(ValueObject):
    """Value object representing a unique merchant-facing notification reference (e.g. NOT-2026-000001)."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Notification reference cannot be empty.")
        clean_val = value.strip()
        if not re.match(r"^NOT-[A-Za-z0-9\-_]+$", clean_val):
            raise ValueError("Notification reference must start with 'NOT-' and contain only alphanumeric characters, hyphens, and underscores.")
        super().__init__({"value": clean_val})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class NotificationChannel(str, Enum):
    EMAIL = "EMAIL"
    WEBHOOK = "WEBHOOK"


class NotificationRecipient(ValueObject):
    """Value object representing the destination recipient mapped and validated by channel type."""
    def __init__(self, channel: NotificationChannel, destination: str):
        if not isinstance(channel, NotificationChannel):
            try:
                channel = NotificationChannel(channel)
            except ValueError:
                raise ValueError(f"Invalid channel: {channel}")

        if not destination or not isinstance(destination, str) or destination.strip() == "":
            raise ValueError("Recipient destination cannot be empty.")
        
        dest = destination.strip()

        if channel == NotificationChannel.EMAIL:
            # Basic email validation regex
            if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", dest):
                raise ValueError(f"Invalid email address: {dest}")
        elif channel == NotificationChannel.WEBHOOK:
            # Webhook recipient must be a valid HTTPS URL
            if not dest.startswith("https://"):
                raise ValueError(f"Webhook recipient must be a valid HTTPS URL, got: {dest}")

        super().__init__({
            "channel": channel,
            "destination": dest
        })

    @property
    def channel(self) -> NotificationChannel:
        return self.props["channel"]

    @property
    def destination(self) -> str:
        return self.props["destination"]


class NotificationType(str, Enum):
    INVOICE_ISSUED = "INVOICE_ISSUED"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    RECEIVABLE_OVERDUE = "RECEIVABLE_OVERDUE"
    SETTLEMENT_SUBMITTED = "SETTLEMENT_SUBMITTED"
    SETTLEMENT_COMPLETED = "SETTLEMENT_COMPLETED"
    SETTLEMENT_FAILED = "SETTLEMENT_FAILED"


class NotificationPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class NotificationTemplateId(ValueObject):
    """Value object wrapping a template identifier."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Template ID cannot be empty.")
        super().__init__({"value": value.strip()})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value


class NotificationPayload(ValueObject):
    """Value object carrying template variables stringified to avoid precision errors."""
    def __init__(self, data: Dict[str, Any]):
        if not isinstance(data, dict):
            raise ValueError("Payload data must be a dictionary.")
        
        cleaned = {}
        for k, v in data.items():
            if not isinstance(k, str):
                raise ValueError("Payload keys must be strings.")
            if v is None:
                cleaned[k] = None
            else:
                cleaned[k] = str(v)

        super().__init__({"data": cleaned})

    @property
    def data(self) -> Dict[str, Any]:
        return self.props["data"]


class NotificationFailureCode(str, Enum):
    EMAIL_PROVIDER_TIMEOUT = "EMAIL_PROVIDER_TIMEOUT"
    WEBHOOK_TIMEOUT = "WEBHOOK_TIMEOUT"
    INVALID_RECIPIENT = "INVALID_RECIPIENT"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class NotificationFailure(ValueObject):
    """Value object encapsulating delivery failure reasons and retry classifications."""
    def __init__(self, code: NotificationFailureCode, message: str, retryable: bool = False):
        if not isinstance(code, NotificationFailureCode):
            try:
                code = NotificationFailureCode(code)
            except ValueError:
                raise ValueError(f"Invalid failure code: {code}")
        
        if not message or not isinstance(message, str) or message.strip() == "":
            raise ValueError("Failure message cannot be empty.")

        super().__init__({
            "code": code,
            "message": message.strip(),
            "retryable": retryable
        })

    @property
    def code(self) -> NotificationFailureCode:
        return self.props["code"]

    @property
    def message(self) -> str:
        return self.props["message"]

    @property
    def retryable(self) -> bool:
        return self.props["retryable"]


class NotificationIdempotencyKey(ValueObject):
    """Value object wrapping a request/event-level idempotency key."""
    def __init__(self, value: str):
        if not value or not isinstance(value, str) or value.strip() == "":
            raise ValueError("Idempotency key cannot be empty.")
        super().__init__({"value": value.strip()})

    @property
    def value(self) -> str:
        return self.props["value"]

    def __str__(self) -> str:
        return self.value
