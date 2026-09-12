import json
from uuid import uuid4
from sqlalchemy import select
from typing import Optional

from ...domain.shared.clock.clock import Clock
from ...domain.organization.value_objects import OrganizationId
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
from ...domain.notification.notification import Notification, NotificationStatus
from ...domain.notification.template import TemplateReference
from ...domain.notification.policies import NotificationPolicy, DEFAULT_POLICY_MAPPING
from ...application.notification.template_service import NotificationTemplateService
from ...application.notification.recipient_service import IRecipientResolver
from ...application.notification.event_mapping import NotificationEventMapper
from ...infrastructure.database.session import session_manager
from ...infrastructure.database.models import (
    OutboxEventDbModel,
    NotificationDbModel,
    PaymentDbModel,
    SettlementDbModel,
    ReceivableDbModel,
    InvoiceDbModel
)
from ...infrastructure.database.repositories import (
    PostgresNotificationRepository,
    PostgresNotificationPreferenceRepository
)


class NotificationEventWorker:
    """
    Background worker that consumes pending transactional outbox events,
    evaluates them against tenant notification policies and preferences,
    and creates target notification aggregates idempotently.
    """
    def __init__(
        self,
        recipient_resolver: IRecipientResolver,
        template_service: NotificationTemplateService,
        preference_repo: PostgresNotificationPreferenceRepository
    ) -> None:
        self.recipient_resolver = recipient_resolver
        self.template_service = template_service
        self.preference_repo = preference_repo
        self.policy = NotificationPolicy(preference_repo)
        self.event_mapper = NotificationEventMapper()

    async def process_pending_events(self) -> int:
        """
        Polls unprocessed outbox events matching the mapped event types,
        resolves merchant preferences and recipients, creates notifications,
        and marks events as processed atomically.
        """
        mapped_event_types = list(DEFAULT_POLICY_MAPPING.keys())

        async with session_manager.session() as session:
            # Query for unprocessed outbox events belonging to our mapped event types
            stmt = (
                select(OutboxEventDbModel)
                .where(OutboxEventDbModel.event_type.in_(mapped_event_types))
                .where(OutboxEventDbModel.processed == False)
                .with_for_update()
            )
            result = await session.execute(stmt)
            events = result.scalars().all()
            if not events:
                return 0

            # Bind repositories to current session
            notification_repo = PostgresNotificationRepository(session)
            # Re-bind preference repo to ensure it uses the transactional session
            self.preference_repo._session = session

            processed_count = 0

            for event in events:
                try:
                    payload = json.loads(event.payload)

                    # 1. Resolve Organization ID with database joins fallback
                    org_id_str = payload.get("organization_id")
                    if not org_id_str:
                        # Fallback resolution based on other context IDs
                        payment_id = payload.get("payment_id")
                        settlement_id = payload.get("settlement_id")
                        receivable_id = payload.get("receivable_id")
                        invoice_id = payload.get("invoice_id")

                        if payment_id:
                            p_stmt = select(PaymentDbModel.organization_id).where(PaymentDbModel.id == payment_id)
                            org_id_str = (await session.execute(p_stmt)).scalar_one_or_none()
                        elif settlement_id:
                            s_stmt = select(SettlementDbModel.organization_id).where(SettlementDbModel.id == settlement_id)
                            org_id_str = (await session.execute(s_stmt)).scalar_one_or_none()
                        elif receivable_id:
                            r_stmt = select(ReceivableDbModel.organization_id).where(ReceivableDbModel.id == receivable_id)
                            org_id_str = (await session.execute(r_stmt)).scalar_one_or_none()
                        elif invoice_id:
                            i_stmt = select(InvoiceDbModel.organization_id).where(InvoiceDbModel.id == invoice_id)
                            org_id_str = (await session.execute(i_stmt)).scalar_one_or_none()

                    if not org_id_str:
                        # Cannot resolve tenant organization, skip
                        event.processed = True
                        continue

                    org_id = OrganizationId(org_id_str)

                    # 2. Idempotency Check: prevent duplicate notification creation
                    idem_stmt = select(NotificationDbModel.id).where(
                        NotificationDbModel.source_event_id == event.id,
                        NotificationDbModel.source_event_type == event.event_type
                    )
                    existing_notif = (await session.execute(idem_stmt)).scalar_one_or_none()
                    if existing_notif:
                        event.processed = True
                        processed_count += 1
                        continue

                    # 3. Check Policy: determine active channels
                    enabled_channels = await self.policy.get_enabled_channels(org_id, event.event_type)
                    if not enabled_channels:
                        event.processed = True
                        processed_count += 1
                        continue

                    mapping = self.policy.get_event_mapping(event.event_type)
                    
                    # 4. Construct variables
                    variables = await self.event_mapper.construct_variables(session, event.event_type, payload)

                    # 5. Render templates and create Notifications for each enabled channel
                    for channel in enabled_channels:
                        recipient_address = await self.recipient_resolver.resolve_recipient(
                            session, org_id, mapping["recipient_type"], channel, payload
                        )
                        if not recipient_address:
                            continue

                        # Try to resolve template & render with fallback
                        subject_mappings = {
                            "InvoiceIssued": "New Invoice Issued",
                            "PaymentConfirmed": "Payment Received",
                            "PaymentReconciled": "Payment Allocation Reconciled",
                            "ReceivableBecameOverdue": "Urgent: Invoice Overdue",
                            "ReceivableCollectionAttentionRequired": "Action Required: Collection Attention Needed",
                            "SettlementSubmitted": "Settlement Request Submitted",
                            "SettlementCompleted": "Settlement Request Completed",
                            "SettlementFailed": "Settlement Request Failed"
                        }
                        default_subject = subject_mappings.get(event.event_type, f"Notification: {event.event_type}")
                        body = f"Event occurred: {event.event_type}"

                        render_res = await self.template_service.render(
                            organization_id=org_id,
                            reference=TemplateReference(mapping["template"]),
                            variables=variables
                        )
                        if render_res.is_success:
                            body = render_res.value

                        # Resolve type enum
                        type_mappings = {
                            "InvoiceIssued": NotificationType.INVOICE_ISSUED,
                            "PaymentConfirmed": NotificationType.PAYMENT_CONFIRMED,
                            "PaymentReconciled": NotificationType.PAYMENT_CONFIRMED,
                            "ReceivableBecameOverdue": NotificationType.RECEIVABLE_OVERDUE,
                            "ReceivableCollectionAttentionRequired": NotificationType.RECEIVABLE_OVERDUE,
                            "DunningStageChanged": NotificationType.RECEIVABLE_OVERDUE,
                            "SettlementSubmitted": NotificationType.SETTLEMENT_SUBMITTED,
                            "SettlementCompleted": NotificationType.SETTLEMENT_COMPLETED,
                            "SettlementFailed": NotificationType.SETTLEMENT_FAILED
                        }
                        notif_type = type_mappings.get(event.event_type, NotificationType.INVOICE_ISSUED)

                        notif = Notification.create(
                            id=NotificationId(str(uuid4())),
                            organization_id=org_id,
                            reference=NotificationReference(f"NOT-EVT-{event.id[:8]}-{channel.value[:3]}".upper()),
                            notification_type=notif_type,
                            recipient=NotificationRecipient(channel, recipient_address),
                            channel=channel,
                            subject=default_subject,
                            template_id=NotificationTemplateId(mapping["template"]),
                            payload=NotificationPayload({"body": body, **variables}),
                            priority=NotificationPriority(mapping["priority"]),
                            idempotency_key=NotificationIdempotencyKey(f"idem-{event.id}-{channel.value}"),
                            source_event_id=event.id,
                            source_event_type=event.event_type,
                            occurred_on=Clock.now()
                        ).value

                        await notification_repo.save(notif)

                    event.processed = True
                    processed_count += 1

                except Exception as e:
                    # Transaction rollback guarantee: raising will abort commit for this block
                    raise e

            await session.commit()
            return processed_count
