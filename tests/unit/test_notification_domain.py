import pytest
from uuid import uuid4
from datetime import datetime

from nomadx.src.domain.notification.notification import Notification, NotificationStatus
from nomadx.src.domain.notification.value_objects import (
    NotificationId,
    NotificationReference,
    NotificationRecipient,
    NotificationChannel,
    NotificationType,
    NotificationPriority,
    NotificationTemplateId,
    NotificationPayload,
    NotificationFailure,
    NotificationFailureCode,
    NotificationIdempotencyKey
)
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.notification.events import (
    NotificationCreated,
    NotificationProcessingStarted,
    NotificationDelivered,
    NotificationDeliveryFailed,
    NotificationRetrying,
    NotificationCancelled
)


def _create_base_params(org_id: str, channel: NotificationChannel, destination: str):
    return {
        "id": NotificationId(str(uuid4())),
        "organization_id": OrganizationId(org_id),
        "reference": NotificationReference("NOT-2026-000001"),
        "notification_type": NotificationType.INVOICE_ISSUED,
        "recipient": NotificationRecipient(channel, destination),
        "channel": channel,
        "subject": "Invoice issued successfully",
        "template_id": NotificationTemplateId("invoice_issued"),
        "payload": NotificationPayload({"customer_name": "Bob", "amount": "50.00"}),
        "priority": NotificationPriority.NORMAL,
        "idempotency_key": NotificationIdempotencyKey(str(uuid4())),
        "source_event_id": str(uuid4()),
        "source_event_type": "InvoiceIssued"
    }


def test_valid_notification_creation():
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.EMAIL, "bob@example.com")
    
    res = Notification.create(**params)
    assert res.is_success is True
    
    n = res.value
    assert n.id == params["id"]
    assert n.organization_id == params["organization_id"]
    assert n.recipient.destination == "bob@example.com"
    assert n.channel == NotificationChannel.EMAIL
    assert n.status == NotificationStatus.PENDING
    assert n.failure is None
    
    # Assert event emitted
    assert len(n.domain_events) == 1
    assert isinstance(n.domain_events[0], NotificationCreated)


def test_webhook_notification_creation():
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.WEBHOOK, "https://example.com/webhook")
    
    res = Notification.create(**params)
    assert res.is_success is True
    assert res.value.channel == NotificationChannel.WEBHOOK


def test_invalid_recipient_formats():
    # 1. Invalid email
    with pytest.raises(ValueError, match="Invalid email address"):
        NotificationRecipient(NotificationChannel.EMAIL, "not-an-email")

    # 2. Invalid Webhook (must be HTTPS)
    with pytest.raises(ValueError, match="Webhook recipient must be a valid HTTPS URL"):
        NotificationRecipient(NotificationChannel.WEBHOOK, "http://insecure-url.com")


def test_recipient_channel_mismatch():
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.EMAIL, "bob@example.com")
    # Manually mismatch channel but keep email recipient object
    params["channel"] = NotificationChannel.WEBHOOK
    
    res = Notification.create(**params)
    assert res.is_success is False
    assert "Recipient channel mismatch" in res.error.message


def test_lifecycle_pending_to_processing():
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.EMAIL, "bob@example.com")
    n = Notification.create(**params).value
    n.clear_domain_events()

    res = n.start_processing()
    assert res.is_success is True
    assert n.status == NotificationStatus.PROCESSING
    assert n.processing_started_at is not None
    assert len(n.domain_events) == 1
    assert isinstance(n.domain_events[0], NotificationProcessingStarted)


def test_lifecycle_processing_to_delivered():
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.EMAIL, "bob@example.com")
    n = Notification.create(**params).value
    n.start_processing().value
    n.clear_domain_events()

    res = n.mark_delivered()
    assert res.is_success is True
    assert n.status == NotificationStatus.DELIVERED
    assert n.delivered_at is not None
    assert len(n.domain_events) == 1
    assert isinstance(n.domain_events[0], NotificationDelivered)


def test_lifecycle_processing_to_failed_and_retry():
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.EMAIL, "bob@example.com")
    n = Notification.create(**params).value
    n.start_processing().value
    n.clear_domain_events()

    # Mark failed with a retryable failure
    failure = NotificationFailure(
        code=NotificationFailureCode.EMAIL_PROVIDER_TIMEOUT,
        message="Email provider service timeout",
        retryable=True
    )
    res = n.mark_failed(failure)
    assert res.is_success is True
    assert n.status == NotificationStatus.FAILED
    assert n.failure.code == NotificationFailureCode.EMAIL_PROVIDER_TIMEOUT
    assert n.failure.retryable is True
    assert len(n.domain_events) == 1
    assert isinstance(n.domain_events[0], NotificationDeliveryFailed)

    n.clear_domain_events()

    # Retry the failed notification
    res_retry = n.retry()
    assert res_retry.is_success is True
    assert n.status == NotificationStatus.RETRYING
    assert len(n.domain_events) == 1
    assert isinstance(n.domain_events[0], NotificationRetrying)

    n.clear_domain_events()

    # Resume processing
    res_proc = n.start_processing()
    assert res_proc.is_success is True
    assert n.status == NotificationStatus.PROCESSING


def test_lifecycle_pending_to_cancelled():
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.EMAIL, "bob@example.com")
    n = Notification.create(**params).value
    n.clear_domain_events()

    res = n.cancel()
    assert res.is_success is True
    assert n.status == NotificationStatus.CANCELLED
    assert len(n.domain_events) == 1
    assert isinstance(n.domain_events[0], NotificationCancelled)


def test_invalid_transitions():
    org_id = str(uuid4())
    params = _create_base_params(org_id, NotificationChannel.EMAIL, "bob@example.com")
    n = Notification.create(**params).value

    # 1. CANCELLED cannot start processing
    n.cancel().value
    res = n.start_processing()
    assert res.is_success is False
    assert "Cannot start processing notification" in res.error.message

    # 2. DELIVERED cannot cancel/retry
    n2 = Notification.create(**params).value
    n2.start_processing().value
    n2.mark_delivered().value
    
    assert n2.cancel().is_success is False
    assert n2.retry().is_success is False
    assert n2.start_processing().is_success is False

    # 3. Permanent FAILED cannot be retried
    n3 = Notification.create(**params).value
    n3.start_processing().value
    perm_failure = NotificationFailure(
        code=NotificationFailureCode.INVALID_RECIPIENT,
        message="Invalid recipient address",
        retryable=False
    )
    n3.mark_failed(perm_failure).value
    assert n3.retry().is_success is False
