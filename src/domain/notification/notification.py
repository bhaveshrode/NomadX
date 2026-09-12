from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional

from ..shared.entities.aggregate_root import AggregateRoot
from ..shared.result.result import Result, ResultError
from ..shared.clock.clock import Clock
from ..organization.value_objects import OrganizationId
from .value_objects import (
    NotificationId,
    NotificationReference,
    NotificationRecipient,
    NotificationChannel,
    NotificationType,
    NotificationPriority,
    NotificationTemplateId,
    NotificationPayload,
    NotificationFailure,
    NotificationIdempotencyKey
)
from .events import (
    NotificationCreated,
    NotificationProcessingStarted,
    NotificationDelivered,
    NotificationDeliveryFailed,
    NotificationRetrying,
    NotificationCancelled,
    NotificationDeadLettered,
    NotificationReplayed
)


class NotificationStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"
    DEAD_LETTERED = "DEAD_LETTERED"


class Notification(AggregateRoot[NotificationId]):
    """
    Aggregate Root representing a Notification message.
    Enforces tenant boundaries, channel configurations, and delivery status transitions.
    """
    def __init__(self, id: NotificationId, props: Dict[str, Any]):
        super().__init__(id)
        self._props = props

    @property
    def organization_id(self) -> OrganizationId:
        return self._props["organization_id"]

    @property
    def reference(self) -> NotificationReference:
        return self._props["reference"]

    @property
    def notification_type(self) -> NotificationType:
        return self._props["notification_type"]

    @property
    def recipient(self) -> NotificationRecipient:
        return self._props["recipient"]

    @property
    def channel(self) -> NotificationChannel:
        return self._props["channel"]

    @property
    def subject(self) -> str:
        return self._props["subject"]

    @property
    def template_id(self) -> NotificationTemplateId:
        return self._props["template_id"]

    @property
    def payload(self) -> NotificationPayload:
        return self._props["payload"]

    @property
    def priority(self) -> NotificationPriority:
        return self._props["priority"]

    @property
    def status(self) -> NotificationStatus:
        return self._props["status"]

    @property
    def idempotency_key(self) -> NotificationIdempotencyKey:
        return self._props["idempotency_key"]

    @property
    def source_event_id(self) -> str:
        return self._props["source_event_id"]

    @property
    def source_event_type(self) -> str:
        return self._props["source_event_type"]

    @property
    def failure(self) -> Optional[NotificationFailure]:
        return self._props.get("failure")

    @property
    def created_at(self) -> datetime:
        return self._props["created_at"]

    @property
    def updated_at(self) -> datetime:
        return self._props["updated_at"]

    @property
    def delivered_at(self) -> Optional[datetime]:
        return self._props.get("delivered_at")

    @property
    def processing_started_at(self) -> Optional[datetime]:
        return self._props.get("processing_started_at")

    @property
    def scheduled_at(self) -> datetime:
        return self._props["scheduled_at"]

    @property
    def retry_count(self) -> int:
        return self._props["retry_count"]

    @property
    def next_retry_at(self) -> Optional[datetime]:
        return self._props.get("next_retry_at")

    @property
    def max_attempts(self) -> int:
        return self._props["max_attempts"]

    @property
    def replay_count(self) -> int:
        return self._props.get("replay_count", 0)

    @classmethod
    def create(
        cls,
        id: NotificationId,
        organization_id: OrganizationId,
        reference: NotificationReference,
        notification_type: NotificationType,
        recipient: NotificationRecipient,
        channel: NotificationChannel,
        subject: str,
        template_id: NotificationTemplateId,
        payload: NotificationPayload,
        priority: NotificationPriority,
        idempotency_key: NotificationIdempotencyKey,
        source_event_id: str,
        source_event_type: str,
        occurred_on: Optional[datetime] = None,
        scheduled_at: Optional[datetime] = None,
        max_attempts: int = 3
    ) -> Result["Notification"]:
        """Factory method constructing a new Notification in PENDING state."""
        if None in (
            id, organization_id, reference, notification_type, recipient, channel,
            subject, template_id, payload, priority, idempotency_key,
            source_event_id, source_event_type
        ):
            return Result.fail(ResultError.validation("All fields are mandatory and cannot be null."))

        if not subject.strip():
            return Result.fail(ResultError.validation("Subject cannot be empty."))

        # Validate that recipient channel matches notification channel
        if recipient.channel != channel:
            return Result.fail(ResultError.validation("Recipient channel mismatch."))

        now = Clock.now()
        props = {
            "organization_id": organization_id,
            "reference": reference,
            "notification_type": notification_type,
            "recipient": recipient,
            "channel": channel,
            "subject": subject.strip(),
            "template_id": template_id,
            "payload": payload,
            "priority": priority,
            "status": NotificationStatus.PENDING,
            "idempotency_key": idempotency_key,
            "source_event_id": source_event_id.strip(),
            "source_event_type": source_event_type.strip(),
            "failure": None,
            "created_at": occurred_on or now,
            "updated_at": occurred_on or now,
            "delivered_at": None,
            "processing_started_at": None,
            "scheduled_at": scheduled_at or occurred_on or now,
            "retry_count": 0,
            "next_retry_at": None,
            "max_attempts": max_attempts
        }

        notification = cls(id, props)
        notification._add_domain_event(NotificationCreated(id, organization_id, occurred_on or now))
        return Result.ok(notification)

    def start_processing(self) -> Result[None]:
        """Transitions PENDING or RETRYING states to PROCESSING state."""
        from .specifications import NotificationCanBeProcessed
        
        spec = NotificationCanBeProcessed()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Cannot start processing notification in state {self.status}."))

        now = Clock.now()
        self._props["status"] = NotificationStatus.PROCESSING
        self._props["processing_started_at"] = now
        self._props["updated_at"] = now
        
        self._add_domain_event(NotificationProcessingStarted(self.id, now))
        return Result.ok(None)

    def mark_delivered(self) -> Result[None]:
        """Transitions PROCESSING state to DELIVERED state."""
        from .specifications import NotificationCanBeDelivered

        spec = NotificationCanBeDelivered()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Cannot deliver notification in state {self.status}."))

        now = Clock.now()
        self._props["status"] = NotificationStatus.DELIVERED
        self._props["delivered_at"] = now
        self._props["updated_at"] = now

        self._add_domain_event(NotificationDelivered(self.id, now))
        return Result.ok(None)

    def mark_failed(self, failure: NotificationFailure) -> Result[None]:
        """Transitions PROCESSING state to FAILED state, capturing failure classification details."""
        if self.status != NotificationStatus.PROCESSING:
            return Result.fail(ResultError.validation(f"Cannot fail notification in state {self.status}."))

        if not isinstance(failure, NotificationFailure):
            return Result.fail(ResultError.validation("Invalid failure object."))

        now = Clock.now()
        self._props["status"] = NotificationStatus.FAILED
        self._props["failure"] = failure
        self._props["updated_at"] = now
        self._props["retry_count"] = self._props.get("retry_count", 0) + 1

        self._add_domain_event(NotificationDeliveryFailed(self.id, failure, now))
        return Result.ok(None)

    def retry(self) -> Result[None]:
        """Transitions a FAILED state with retryable failure flags to RETRYING."""
        from .specifications import NotificationCanBeRetried

        spec = NotificationCanBeRetried()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Cannot retry notification in state {self.status}."))

        now = Clock.now()
        self._props["status"] = NotificationStatus.RETRYING
        self._props["updated_at"] = now

        self._add_domain_event(NotificationRetrying(self.id, now))
        return Result.ok(None)

    def schedule_retry(self, next_retry_at: datetime) -> Result[None]:
        """Schedules the next retry by transitioning FAILED state to RETRYING and setting the timestamp."""
        if self.status != NotificationStatus.FAILED:
            return Result.fail(ResultError.validation(f"Cannot schedule retry in state {self.status}."))

        self._props["next_retry_at"] = next_retry_at
        return self.retry()

    def cancel(self) -> Result[None]:
        """Transitions a PENDING state to CANCELLED state."""
        from .specifications import NotificationCanBeCancelled

        spec = NotificationCanBeCancelled()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Cannot cancel notification in state {self.status}."))

        now = Clock.now()
        self._props["status"] = NotificationStatus.CANCELLED
        self._props["updated_at"] = now

        self._add_domain_event(NotificationCancelled(self.id, now))
        return Result.ok(None)

    def mark_dead_lettered(self, reason: str) -> Result[None]:
        """Transitions FAILED or PROCESSING state to DEAD_LETTERED state on retry exhaustion."""
        if self.status not in (NotificationStatus.PROCESSING, NotificationStatus.FAILED):
            return Result.fail(ResultError.validation(f"Cannot dead-letter notification in state {self.status}."))

        now = Clock.now()
        self._props["status"] = NotificationStatus.DEAD_LETTERED
        self._props["updated_at"] = now
        # Keep failure details for auditability/last failure logs
        if not self._props.get("failure"):
            from .value_objects import NotificationFailureCode
            self._props["failure"] = NotificationFailure(
                code=NotificationFailureCode.UNKNOWN_ERROR,
                message=reason,
                retryable=False
            )
        self._add_domain_event(NotificationDeadLettered(self.id, reason, now))
        return Result.ok(None)

    def replay(self) -> Result[None]:
        """Resets the delivery state and transitions to PENDING to trigger redelivery."""
        if self.status in (NotificationStatus.PENDING, NotificationStatus.PROCESSING):
            return Result.fail(ResultError.validation(f"Cannot replay notification in active state {self.status}."))

        now = Clock.now()
        self._props["status"] = NotificationStatus.PENDING
        self._props["retry_count"] = 0
        self._props["next_retry_at"] = None
        self._props["delivered_at"] = None
        self._props["processing_started_at"] = None
        self._props["failure"] = None
        self._props["replay_count"] = self._props.get("replay_count", 0) + 1
        self._props["updated_at"] = now
        self._props["scheduled_at"] = now

        self._add_domain_event(NotificationReplayed(self.id, now))
        return Result.ok(None)
