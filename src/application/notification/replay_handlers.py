from ...domain.shared.result.result import Result, ResultError
from ...domain.notification.value_objects import NotificationId
from ...domain.notification.repositories import INotificationRepository
from ...domain.notification.notification import Notification
from ...presentation.api.dependencies.context import ExecutionContext
from .replay_commands import ReplayNotificationCommand


class ReplayNotificationHandler:
    """
    Handles replaying/redriving completed, failed, or dead-lettered notifications.
    Enforces strict role authorization and organization tenant boundaries.
    """
    def __init__(self, notification_repo: INotificationRepository) -> None:
        self.notification_repo = notification_repo

    async def handle(self, cmd: ReplayNotificationCommand, ctx: ExecutionContext) -> Result[Notification]:
        # 1. Enforce strict role authorization
        if ctx.role not in ("ADMIN", "OWNER", "SYSTEM"):
            return Result.fail(ResultError.unauthorized("Action requires ADMIN or OWNER permissions."))

        # 2. Retrieve aggregate
        notif_id = NotificationId(cmd.notification_id)
        notification = await self.notification_repo.get_by_id(notif_id)
        if not notification:
            return Result.fail(ResultError.not_found("Notification not found."))

        # 3. Enforce tenant isolation
        if notification.organization_id.value != ctx.organization_id:
            return Result.fail(ResultError.unauthorized("Tenant boundary violation."))

        # 4. Mutate aggregate state to reset delivery controls
        res = notification.replay()
        if not res.is_failure and res.is_success is False: # Check failure condition safely
            return res
        if res.is_failure:
            return Result.fail(res.error)

        # 5. Persist aggregate changes
        await self.notification_repo.save(notification)
        return Result.ok(notification)
