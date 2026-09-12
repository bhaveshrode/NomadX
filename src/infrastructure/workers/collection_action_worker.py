import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.database.session import session_manager
from ...infrastructure.database.models import (
    ReceivableDbModel,
    InvoiceDbModel,
    OutboxEventDbModel,
    ReceivableCollectionActionDbModel
)
from ...domain.shared.clock.clock import Clock
from ...infrastructure.database.repositories import (
    PostgresCollectionActionRepository,
    PostgresNotificationRepository
)
from ...domain.organization.value_objects import OrganizationId
from ...domain.receivable.collection_action import CollectionAction, CollectionActionId
from ...domain.receivable.value_objects import ReceivableId
from ...domain.notification.value_objects import (
    NotificationId,
    NotificationReference,
    NotificationRecipient,
    NotificationChannel,
    NotificationType,
    NotificationPriority,
    NotificationTemplateId,
    NotificationPayload,
    NotificationIdempotencyKey
)
from ...domain.notification.notification import Notification
from ...domain.notification.template import TemplateReference
from ...application.notification.template_service import NotificationTemplateService
from ...application.notification.recipient_service import IRecipientResolver


class CollectionActionWorker:
    """
    Worker evaluating and executing scheduled collection actions,
    handling retry/backoff mechanics and cross-context integrations.
    """
    def __init__(
        self,
        recipient_resolver: IRecipientResolver,
        template_service: NotificationTemplateService
    ):
        self.recipient_resolver = recipient_resolver
        self.template_service = template_service

    async def process_actions(self, limit: int = 50) -> int:
        current_time = Clock.now()
        print(f"DEBUG WORKER CLOCK: {current_time}, type={type(current_time)}")
        async with session_manager.session() as session:
            collection_action_repo = PostgresCollectionActionRepository(session)
            notification_repo = PostgresNotificationRepository(session)

            # Claim pending actions safely using SKIP LOCKED row lock
            actions = await collection_action_repo.claim_pending_actions(limit, current_time)
            if not actions:
                return 0

            processed_count = 0

            for action in actions:
                # Mark as processing
                action.start_processing()
                await collection_action_repo.save(action)
                await session.flush()

                try:
                    # 1. Load context details
                    rec_stmt = select(ReceivableDbModel).where(ReceivableDbModel.id == action.receivable_id.value)
                    rec_model = (await session.execute(rec_stmt)).scalar_one_or_none()
                    if not rec_model:
                        raise ValueError(f"Receivable {action.receivable_id.value} not found for collection action.")

                    inv_stmt = select(InvoiceDbModel).where(InvoiceDbModel.id == rec_model.invoice_id)
                    inv_model = (await session.execute(inv_stmt)).scalar_one_or_none()
                    if not inv_model:
                        raise ValueError(f"Invoice {rec_model.invoice_id} not found for collection action.")

                    # 2. Execute Action Type
                    if action.action_type in ("SEND_REMINDER", "SEND_FINAL_NOTICE"):
                        # Resolve Customer Email address via IRecipientResolver
                        recipient_address = await self.recipient_resolver.resolve_recipient(
                            session,
                            action.organization_id,
                            "CUSTOMER",
                            NotificationChannel.EMAIL,
                            {"invoice_id": rec_model.invoice_id}
                        )
                        if not recipient_address:
                            raise ValueError(f"Could not resolve recipient email for invoice {rec_model.invoice_id}")

                        # Calculate variables
                        ct = Clock.now().replace(tzinfo=None) if Clock.now().tzinfo else Clock.now()
                        dd = rec_model.due_date.replace(tzinfo=None) if rec_model.due_date.tzinfo else rec_model.due_date
                        days_overdue = (ct - dd).days
                        if days_overdue < 0:
                            days_overdue = 0

                        variables = {
                            "customer_name": inv_model.billing_name or "Customer",
                            "invoice_reference": rec_model.invoice_reference,
                            "receivable_reference": rec_model.reference,
                            "outstanding_amount": f"{Decimal(rec_model.outstanding_amount):.2f}",
                            "currency": rec_model.currency,
                            "due_date": rec_model.due_date.strftime("%Y-%m-%d"),
                            "days_overdue": str(days_overdue),
                            "dunning_stage": rec_model.dunning_stage,
                            "payment_information": "Please follow payment instructions on the invoice."
                        }

                        # Render template
                        template_ref = "receivable.overdue"
                        subject = "Reminder: Invoice Overdue"
                        if action.action_type == "SEND_FINAL_NOTICE":
                            subject = "Final Notice: Invoice Overdue"

                        render_res = await self.template_service.render(
                            organization_id=action.organization_id,
                            reference=TemplateReference(template_ref),
                            variables=variables
                        )
                        body = render_res.value if render_res.is_success else f"Invoice {rec_model.invoice_reference} is overdue. Please pay immediately."

                        # Construct Notification aggregate
                        notif_id = str(uuid4())
                        notif = Notification.create(
                            id=NotificationId(notif_id),
                            organization_id=action.organization_id,
                            reference=NotificationReference(f"NOT-DUN-{action.id.value[:8]}".upper()),
                            notification_type=NotificationType.RECEIVABLE_OVERDUE,
                            recipient=NotificationRecipient(NotificationChannel.EMAIL, recipient_address),
                            channel=NotificationChannel.EMAIL,
                            subject=subject,
                            template_id=NotificationTemplateId(template_ref),
                            payload=NotificationPayload({"body": body, **variables}),
                            priority=NotificationPriority.HIGH,
                            idempotency_key=NotificationIdempotencyKey(f"idem-action-{action.id.value}"),
                            source_event_id=action.id.value,
                            source_event_type="CollectionAction",
                            occurred_on=Clock.now()
                        ).value

                        await notification_repo.save(notif)

                    elif action.action_type in ("ESCALATE_COLLECTION", "CREATE_WORKFLOW"):
                        # Emit Outbox Event to trigger workflow engines
                        event_type = "ReceivableCollectionAttentionRequired" if action.action_type == "CREATE_WORKFLOW" else "ReceivableEscalationRequired"
                        outbox_event = OutboxEventDbModel(
                            id=str(uuid4()),
                            event_type=event_type,
                            payload=json.dumps({
                                "receivable_id": action.receivable_id.value,
                                "organization_id": action.organization_id.value,
                                "occurred_on": Clock.now().isoformat()
                            }),
                            processed=False,
                            created_at=Clock.now()
                        )
                        session.add(outbox_event)

                    else:
                        raise ValueError(f"Unsupported action type: {action.action_type}")

                    # Complete Action
                    action.complete(Clock.now())
                    await collection_action_repo.save(action)
                    processed_count += 1

                except Exception as e:
                    # Classify failure
                    error_str = str(e)
                    is_permanent = (
                        isinstance(e, ValueError) or 
                        "not found" in error_str.lower() or 
                        "unsupported" in error_str.lower() or
                        "invalid" in error_str.lower()
                    )
                    failure_classification = "PERMANENT" if is_permanent else "TRANSIENT"

                    # Calculate backoff delay
                    attempts = action.attempt_count + 1
                    delay_map = {1: 60, 2: 300, 3: 900}
                    delay_seconds = delay_map.get(attempts, 900)

                    next_retry_at = Clock.now() + timedelta(seconds=delay_seconds)
                    action.fail(
                        error_message=error_str,
                        next_retry_at=next_retry_at,
                        failure_classification=failure_classification
                    )
                    await collection_action_repo.save(action)

            await session.commit()
            return processed_count

    async def recover_stuck_actions(self, threshold_minutes: int = 15) -> int:
        """Finds collection actions stuck in PROCESSING status and resets them."""
        cutoff = Clock.now() - timedelta(minutes=threshold_minutes)
        cutoff_naive = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff

        async with session_manager.session() as session:
            stmt = select(ReceivableCollectionActionDbModel).where(
                ReceivableCollectionActionDbModel.status == "PROCESSING",
                ReceivableCollectionActionDbModel.created_at <= cutoff_naive
            )
            res = await session.execute(stmt)
            stuck_models = res.scalars().all()
            if not stuck_models:
                return 0

            collection_action_repo = PostgresCollectionActionRepository(session)
            count = 0
            for model in stuck_models:
                action = collection_action_repo._to_domain(model)
                attempts = action.attempt_count + 1
                delay_map = {1: 60, 2: 300, 3: 900}
                delay_seconds = delay_map.get(attempts, 900)
                next_retry_at = Clock.now() + timedelta(seconds=delay_seconds)

                action.fail(
                    error_message="Action recovered from stuck PROCESSING state.",
                    next_retry_at=next_retry_at,
                    failure_classification="TRANSIENT"
                )
                await collection_action_repo.save(action)
                count += 1

            await session.commit()
            return count
