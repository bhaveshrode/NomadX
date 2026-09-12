from ...domain.organization.value_objects import OrganizationId
from ...domain.notification.notification import Notification, NotificationStatus
from ...domain.notification.repositories import INotificationRepository
from ...domain.notification.value_objects import (
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
from ...domain.shared.result.result import Result
from ...presentation.api.dependencies.context import ExecutionContext

from .commands import (
    CreateNotificationCommand,
    StartNotificationProcessingCommand,
    MarkNotificationDeliveredCommand,
    MarkNotificationFailedCommand,
    RetryNotificationCommand,
    CancelNotificationCommand
)
from .errors import NotificationErrors


class CreateNotificationHandler:
    """
    Handler to create a new Notification.
    Idempotent: returns existing notification if already created for this key.
    """
    def __init__(self, repo: INotificationRepository):
        self._repo = repo

    async def handle(self, command: CreateNotificationCommand, ctx: ExecutionContext) -> Result[Notification]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(NotificationErrors.unauthorized_organization_access("Context tenant mismatch."))

        # 1. Idempotency Key check
        existing_idem = await self._repo.find_by_idempotency_key(
            OrganizationId(command.organization_id),
            NotificationIdempotencyKey(command.idempotency_key)
        )
        if existing_idem:
            if existing_idem.organization_id.value != ctx.organization_id:
                return Result.fail(NotificationErrors.unauthorized_organization_access("Idempotency key belongs to another tenant."))
            return Result.ok(existing_idem)

        # 2. Source Event check
        if command.source_event_id and command.source_event_type:
            existing_events = await self._repo.find_by_source_event(
                command.source_event_id,
                command.source_event_type
            )
            for existing_ev in existing_events:
                if existing_ev.organization_id.value == ctx.organization_id:
                    return Result.ok(existing_ev)

        # 3. Value Objects Reconstruction
        try:
            channel = NotificationChannel(command.channel)
            recipient = NotificationRecipient(channel, command.recipient_destination)
            ref = NotificationReference(command.reference)
            notification_type = NotificationType(command.notification_type)
            template_id = NotificationTemplateId(command.template_id)
            payload = NotificationPayload(command.payload)
            priority = NotificationPriority(command.priority)
            idem_key = NotificationIdempotencyKey(command.idempotency_key)
        except ValueError as err:
            return Result.fail(NotificationErrors.invalid_notification_data(str(err)))

        # 4. Create Notification
        create_res = Notification.create(
            id=NotificationId(command.notification_id),
            organization_id=OrganizationId(command.organization_id),
            reference=ref,
            notification_type=notification_type,
            recipient=recipient,
            channel=channel,
            subject=command.subject,
            template_id=template_id,
            payload=payload,
            priority=priority,
            idempotency_key=idem_key,
            source_event_id=command.source_event_id,
            source_event_type=command.source_event_type
        )
        if create_res.is_failure:
            return Result.fail(create_res.error)

        notification = create_res.value
        await self._repo.save(notification)
        return Result.ok(notification)


class StartNotificationProcessingHandler:
    """Handler to move Notification from PENDING/RETRYING to PROCESSING state."""
    def __init__(self, repo: INotificationRepository):
        self._repo = repo

    async def handle(self, command: StartNotificationProcessingCommand, ctx: ExecutionContext) -> Result[Notification]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(NotificationErrors.unauthorized_organization_access())

        # Load aggregate with pessimistic lock
        notification = await self._repo.get_by_id(NotificationId(command.notification_id), lock=True)
        if not notification:
            return Result.fail(NotificationErrors.notification_not_found())

        # Tenant Guard
        if notification.organization_id.value != ctx.organization_id:
            return Result.fail(NotificationErrors.unauthorized_organization_access())

        res = notification.start_processing()
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(notification)
        return Result.ok(notification)


class MarkNotificationDeliveredHandler:
    """Handler to mark Notification delivered successfully."""
    def __init__(self, repo: INotificationRepository):
        self._repo = repo

    async def handle(self, command: MarkNotificationDeliveredCommand, ctx: ExecutionContext) -> Result[Notification]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(NotificationErrors.unauthorized_organization_access())

        # Load aggregate with pessimistic lock
        notification = await self._repo.get_by_id(NotificationId(command.notification_id), lock=True)
        if not notification:
            return Result.fail(NotificationErrors.notification_not_found())

        # Tenant Guard
        if notification.organization_id.value != ctx.organization_id:
            return Result.fail(NotificationErrors.unauthorized_organization_access())

        res = notification.mark_delivered()
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(notification)
        return Result.ok(notification)


class MarkNotificationFailedHandler:
    """Handler to record delivery failure details."""
    def __init__(self, repo: INotificationRepository):
        self._repo = repo

    async def handle(self, command: MarkNotificationFailedCommand, ctx: ExecutionContext) -> Result[Notification]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(NotificationErrors.unauthorized_organization_access())

        # Load aggregate with pessimistic lock
        notification = await self._repo.get_by_id(NotificationId(command.notification_id), lock=True)
        if not notification:
            return Result.fail(NotificationErrors.notification_not_found())

        # Tenant Guard
        if notification.organization_id.value != ctx.organization_id:
            return Result.fail(NotificationErrors.unauthorized_organization_access())

        try:
            failure_code = NotificationFailureCode(command.failure_code)
            failure = NotificationFailure(failure_code, command.failure_message, command.failure_retryable)
        except ValueError as err:
            return Result.fail(NotificationErrors.invalid_notification_data(str(err)))

        res = notification.mark_failed(failure)
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(notification)
        return Result.ok(notification)


class RetryNotificationHandler:
    """Handler to retry a failed retryable Notification."""
    def __init__(self, repo: INotificationRepository):
        self._repo = repo

    async def handle(self, command: RetryNotificationCommand, ctx: ExecutionContext) -> Result[Notification]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(NotificationErrors.unauthorized_organization_access())

        # Load aggregate with pessimistic lock
        notification = await self._repo.get_by_id(NotificationId(command.notification_id), lock=True)
        if not notification:
            return Result.fail(NotificationErrors.notification_not_found())

        # Tenant Guard
        if notification.organization_id.value != ctx.organization_id:
            return Result.fail(NotificationErrors.unauthorized_organization_access())

        res = notification.retry()
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(notification)
        return Result.ok(notification)


class CancelNotificationHandler:
    """Handler to cancel a PENDING notification."""
    def __init__(self, repo: INotificationRepository):
        self._repo = repo

    async def handle(self, command: CancelNotificationCommand, ctx: ExecutionContext) -> Result[Notification]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(NotificationErrors.unauthorized_organization_access())

        # Load aggregate with pessimistic lock
        notification = await self._repo.get_by_id(NotificationId(command.notification_id), lock=True)
        if not notification:
            return Result.fail(NotificationErrors.notification_not_found())

        # Tenant Guard
        if notification.organization_id.value != ctx.organization_id:
            return Result.fail(NotificationErrors.unauthorized_organization_access())

        res = notification.cancel()
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(notification)
        return Result.ok(notification)
