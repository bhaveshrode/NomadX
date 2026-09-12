import json
from abc import ABC, abstractmethod
from typing import Any, Dict
from uuid import uuid4
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.workflow.actions import ActionType, WorkflowAction
from ...presentation.api.dependencies.context import ExecutionContext
from ...application.notification.commands import CreateNotificationCommand
from ...application.notification.handlers import CreateNotificationHandler
from ...infrastructure.database.repositories import PostgresNotificationRepository
from ...application.settlement.commands import CreateSettlementCommand
from ...application.settlement.handlers import CreateSettlementHandler
from ...application.settlement.services import SettlementAvailabilityService
from ...infrastructure.database.repositories import PostgresSettlementRepository, PostgresPaymentRepository
from .condition_evaluator import get_payload_value


class IWorkflowActionExecutor(ABC):
    """
    Interface/Port for executing workflow actions within the active transaction.
    """
    @abstractmethod
    async def execute(
        self,
        action: WorkflowAction,
        session: AsyncSession,
        execution_id: str,
        organization_id: str,
        source_event_id: str,
        source_event_type: str,
        payload: Dict[str, Any],
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        """Executes a single action and returns execution metrics/results."""
        pass


class WorkflowActionExecutor(IWorkflowActionExecutor):
    """
    Action executor dynamically binding repository/services to the active session.
    Routes SEND_NOTIFICATION and CREATE_SETTLEMENT actions transactionally.
    """

    async def execute(
        self,
        action: WorkflowAction,
        session: AsyncSession,
        execution_id: str,
        organization_id: str,
        source_event_id: str,
        source_event_type: str,
        payload: Dict[str, Any],
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        action_type = action.action_type

        if action_type == ActionType.SEND_NOTIFICATION:
            # Resolve recipient target safely from parameters or payload
            recipient = action.parameters.get("recipient")
            if not recipient:
                recipient = (
                    get_payload_value(payload, "customer_email") or
                    get_payload_value(payload, "email") or
                    get_payload_value(payload, "billing_snapshot.email") or
                    get_payload_value(payload, "recipient") or
                    "default@nomadx.com"
                )

            notif_type = action.parameters.get("notification_type")
            if not notif_type:
                import re
                s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', source_event_type)
                notif_type = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).upper()
                valid_types = {"INVOICE_ISSUED", "PAYMENT_CONFIRMED", "PAYMENT_FAILED", "RECEIVABLE_OVERDUE", "SETTLEMENT_SUBMITTED", "SETTLEMENT_COMPLETED", "SETTLEMENT_FAILED"}
                if notif_type not in valid_types:
                    notif_type = "INVOICE_ISSUED"

            notif_id = str(uuid4())
            cmd = CreateNotificationCommand(
                notification_id=notif_id,
                organization_id=organization_id,
                reference=f"NOT-{str(uuid4())[:8]}",
                notification_type=notif_type,
                recipient_destination=recipient,
                channel=action.parameters.get("channel", "EMAIL"),
                subject=action.parameters.get("subject", "Automated Workflow Notification"),
                template_id=action.parameters.get("template_id", "default-template"),
                payload=payload,
                priority=action.parameters.get("priority", "NORMAL"),
                idempotency_key=f"wf-exec-{execution_id}-{action_type.name}",
                source_event_id=source_event_id,
                source_event_type=source_event_type
            )

            repo = PostgresNotificationRepository(session)
            handler = CreateNotificationHandler(repo)
            res = await handler.handle(cmd, ctx)
            if res.is_failure:
                raise ValueError(f"SEND_NOTIFICATION action failed: {res.error.message}")

            return {"notification_id": notif_id, "status": "success"}

        elif action_type == ActionType.CREATE_SETTLEMENT:
            # Extract payment ID from payload
            payment_id = action.parameters.get("payment_id") or payload.get("payment_id") or payload.get("id")
            if not payment_id:
                raise ValueError("CREATE_SETTLEMENT action failed: payment_id not found in action or payload context.")

            settlement_id = str(uuid4())
            cmd = CreateSettlementCommand(
                settlement_id=settlement_id,
                organization_id=organization_id,
                payment_id=payment_id,
                amount=Decimal(action.parameters.get("amount", payload.get("observed_amount", payload.get("amount", "0.00")))),
                asset_code=action.parameters.get("asset_code", payload.get("asset_code", "USDC")),
                asset_type=action.parameters.get("asset_type", "DIGITAL_ASSET"),
                asset_decimals=int(action.parameters.get("asset_decimals", 6)),
                network=action.parameters.get("network", payload.get("network_code", "BASE")),
                destination_type=action.parameters.get("destination_type", "BLOCKCHAIN_ADDRESS"),
                destination_identifier=action.parameters.get("destination_identifier", payload.get("receiver", "0xDefaultReceiver")),
                method=action.parameters.get("method", "BLOCKCHAIN"),
                reference=f"SETL-{str(uuid4())[:8]}",
                idempotency_key=f"wf-exec-{execution_id}-{action_type.name}"
            )

            settlement_repo = PostgresSettlementRepository(session)
            payment_repo = PostgresPaymentRepository(session)
            availability_svc = SettlementAvailabilityService(settlement_repo, payment_repo)
            handler = CreateSettlementHandler(settlement_repo, payment_repo, availability_svc)

            res = await handler.handle(cmd, ctx)
            if res.is_failure:
                raise ValueError(f"CREATE_SETTLEMENT action failed: {res.error.message}")

            return {"settlement_id": settlement_id, "status": "success"}

        else:
            raise ValueError(f"Unsupported action type: {action_type}")
