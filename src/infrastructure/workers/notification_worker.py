from uuid import uuid4
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import select, func

from ...domain.shared.clock.clock import Clock
from ...infrastructure.database.session import session_manager
from ...infrastructure.database.models import NotificationDbModel, NotificationDeliveryAttemptDbModel
from ...infrastructure.database.repositories import PostgresNotificationRepository
from ...domain.notification.value_objects import NotificationId, NotificationFailure, NotificationFailureCode
from ...infrastructure.notification.interfaces import INotificationDeliveryAdapter
from ...infrastructure.notification.models import NotificationDeliveryRequest, NotificationDeliveryResult
from ...infrastructure.notification.circuit_breaker import ProviderCircuitBreaker


class BackoffPolicy:
    """
    Orchestrates time-aware retry delays based on the current attempt count.
    Attempt 1: 1 minute delay
    Attempt 2: 5 minutes delay
    Attempt 3: 15 minutes delay
    """
    def next_retry_at(self, attempt: int, current_time: datetime) -> datetime:
        intervals = {1: 1, 2: 5, 3: 15}
        minutes = intervals.get(attempt, 15)
        return current_time + timedelta(minutes=minutes)


class NotificationWorker:
    """
    Background worker that queries, locks, and executes scheduled notifications
    via a configured INotificationDeliveryAdapter, ensuring transactional safety,
    execution idempotency, and retry backoff delays.
    """
    def __init__(
        self,
        delivery_adapter: INotificationDeliveryAdapter,
        backoff_policy: Optional[BackoffPolicy] = None,
        rate_limits: Optional[dict] = None
    ) -> None:
        self.delivery_adapter = delivery_adapter
        self.backoff_policy = backoff_policy or BackoffPolicy()
        # Default rate limits: 100/min for email, 50/min for webhooks
        self.rate_limits = rate_limits or {"EMAIL": 100, "WEBHOOK": 50}

    async def _check_rate_limit(self, session, provider_name: str, channel: str) -> bool:
        limit = self.rate_limits.get(channel, 50)
        one_minute_ago = Clock.now() - timedelta(minutes=1)
        stmt = select(func.count(NotificationDeliveryAttemptDbModel.id)).where(
            NotificationDeliveryAttemptDbModel.provider == provider_name,
            NotificationDeliveryAttemptDbModel.started_at >= one_minute_ago
        )
        count = (await session.execute(stmt)).scalar() or 0
        return count < limit

    async def process_pending_notifications(self) -> int:
        """
        Queries all notifications matching scheduled parameters, locks and transitions them,
        dispatches to adapters outside DB transaction boundaries, logs delivery attempts,
        and commits final statuses atomically.
        """
        now = Clock.now()
        provider_name = self.delivery_adapter.__class__.__name__

        # 1. Fetch pending/retrying/processing notification IDs with scheduling filters
        async with session_manager.session() as session:
            stmt = select(NotificationDbModel.id).where(
                ((NotificationDbModel.status == "PENDING") & (NotificationDbModel.scheduled_at <= now)) |
                ((NotificationDbModel.status == "RETRYING") & (NotificationDbModel.next_retry_at <= now)) |
                (NotificationDbModel.status == "PROCESSING")
            )
            result = await session.execute(stmt)
            notification_ids = result.scalars().all()

        processed_count = 0

        # 2. Iterate and process each notification individually
        for n_id in notification_ids:
            attempt_number = 1
            started_at = Clock.now()

            # Transaction 1: Lock row, fetch details, check rate limits & circuit availability
            async with session_manager.session() as session:
                # Re-bind circuit breaker to session
                cb = ProviderCircuitBreaker(session)
                
                # Check Circuit Breaker availability
                if not await cb.check_availability(provider_name):
                    continue

                repo = PostgresNotificationRepository(session)
                notification = await repo.get_by_id(NotificationId(n_id), lock=True)
                if not notification or notification.status.value not in ("PENDING", "RETRYING", "PROCESSING"):
                    # State changed concurrently, skip
                    continue

                # Check Rate Limiting
                if not await self._check_rate_limit(session, provider_name, notification.channel.value):
                    # Rate limit exceeded, skip this sweep to defer delivery
                    continue

                attempt_number = notification.retry_count + 1

                if notification.status.value in ("PENDING", "RETRYING"):
                    res_proc = notification.start_processing()
                    if res_proc.is_failure:
                        continue
                    await repo.save(notification)
                    await session.commit()
                else:
                    # Already in PROCESSING, just release lock
                    await session.commit()

                request = NotificationDeliveryRequest(
                    notification_id=notification.id.value,
                    organization_id=notification.organization_id.value,
                    channel=notification.channel.value,
                    recipient=notification.recipient.destination,
                    notification_type=notification.notification_type.value,
                    subject=notification.subject,
                    payload=dict(notification.payload.data),
                    idempotency_key=notification.idempotency_key.value
                )

            # External Delivery: Call delivery adapter outside the DB transaction boundary
            try:
                delivery_result = await self.delivery_adapter.deliver(request)
            except Exception as e:
                delivery_result = NotificationDeliveryResult(
                    is_success=False,
                    failure_code="UNKNOWN_ERROR",
                    failure_message=str(e),
                    failure_retryable=True
                )

            completed_at = Clock.now()

            # Transaction 2: Re-acquire lock, save status transitions, log attempt history, record CB health
            async with session_manager.session() as session:
                cb = ProviderCircuitBreaker(session)
                repo = PostgresNotificationRepository(session)
                notification = await repo.get_by_id(NotificationId(n_id), lock=True)
                if not notification or notification.status.value != "PROCESSING":
                    # Status mutated concurrently, skip
                    continue

                if delivery_result.is_success:
                    notification.mark_delivered()
                    await cb.record_success(provider_name)
                else:
                    await cb.record_failure(provider_name)

                    try:
                        code_enum = NotificationFailureCode(delivery_result.failure_code)
                    except ValueError:
                        code_enum = NotificationFailureCode.UNKNOWN_ERROR

                    failure = NotificationFailure(
                        code=code_enum,
                        message=delivery_result.failure_message or "Delivery failed without detailed message.",
                        retryable=delivery_result.failure_retryable
                    )
                    notification.mark_failed(failure)

                    # Trigger time-aware backoff retry if retryable
                    if failure.retryable:
                        if notification.retry_count < notification.max_attempts:
                            next_retry = self.backoff_policy.next_retry_at(notification.retry_count, completed_at)
                            notification.schedule_retry(next_retry)
                        else:
                            # Exceeded max attempts -> Dead Letter
                            notification.mark_dead_lettered(
                                f"Delivery failure: {failure.message} (Retry count: {notification.retry_count})"
                            )
                    else:
                        # Permanent provider failure -> stays FAILED (no retry, not dead-lettered)
                        pass

                # Persist delivery attempt history record
                attempt_model = NotificationDeliveryAttemptDbModel(
                    id=str(uuid4()),
                    notification_id=notification.id.value,
                    organization_id=notification.organization_id.value,
                    attempt_number=attempt_number,
                    channel=notification.channel.value,
                    provider=provider_name,
                    started_at=started_at,
                    completed_at=completed_at,
                    is_success=delivery_result.is_success,
                    provider_reference=delivery_result.provider_reference,
                    failure_code=delivery_result.failure_code,
                    failure_message=delivery_result.failure_message
                )
                session.add(attempt_model)

                await repo.save(notification)
                await session.commit()
                processed_count += 1

        return processed_count
