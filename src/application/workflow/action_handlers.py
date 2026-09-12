import json
from abc import ABC, abstractmethod
from typing import Any, Dict
from uuid import uuid4
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from ...presentation.api.dependencies.context import ExecutionContext
from ...application.notification.commands import CreateNotificationCommand
from ...application.notification.handlers import CreateNotificationHandler
from ...infrastructure.database.repositories import PostgresNotificationRepository
from ...application.settlement.commands import CreateSettlementCommand
from ...application.settlement.handlers import CreateSettlementHandler
from ...application.settlement.services import SettlementAvailabilityService
from ...infrastructure.database.repositories import PostgresSettlementRepository, PostgresPaymentRepository


class IWorkflowActionHandler(ABC):
    """
    Interface for implementing pluggable workflow action execution handlers.
    """
    @abstractmethod
    async def execute(
        self,
        parameters: Dict[str, Any],
        session: AsyncSession,
        execution_context: Dict[str, Any],
        payload: Dict[str, Any],
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        """
        Executes a concrete workflow action and returns outcomes/audit results.
        """
        pass


class SendNotificationActionHandler(IWorkflowActionHandler):
    """
    Workflow action handler resolving and executing SEND_NOTIFICATION.
    """
    async def execute(
        self,
        parameters: Dict[str, Any],
        session: AsyncSession,
        execution_context: Dict[str, Any],
        payload: Dict[str, Any],
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        recipient = parameters.get("recipient")
        if not recipient:
            recipient = payload.get("customer_email") or payload.get("email")
            if not recipient and isinstance(payload.get("billing_snapshot"), dict):
                recipient = payload.get("billing_snapshot", {}).get("email")
            if not recipient:
                recipient = "default@nomadx.com"

        notif_id = str(uuid4())
        
        # Dynamically map case-insensitive NotificationType
        notif_type = parameters.get("notification_type")
        if not notif_type:
            import re
            source_event_type = execution_context.get("source_event_type", "InvoiceIssued")
            s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', source_event_type)
            notif_type = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).upper()
            valid_types = {"INVOICE_ISSUED", "PAYMENT_CONFIRMED", "PAYMENT_FAILED", "RECEIVABLE_OVERDUE", "SETTLEMENT_SUBMITTED", "SETTLEMENT_COMPLETED", "SETTLEMENT_FAILED"}
            if not notif_type in valid_types:
                notif_type = "INVOICE_ISSUED"

        # Build correlation/ancestry chain metadata
        correlation_id = execution_context.get("correlation_id")
        ancestry = list(execution_context.get("ancestry", []))
        
        # Format outbox custom metadata to prevent loop cycles
        cloned_payload = dict(payload)
        cloned_payload["_metadata"] = {
            "correlation_id": correlation_id,
            "ancestry": ancestry
        }

        cmd = CreateNotificationCommand(
            notification_id=notif_id,
            organization_id=execution_context["organization_id"],
            reference=f"NOT-{str(uuid4())[:8]}",
            notification_type=notif_type,
            recipient_destination=recipient,
            channel=parameters.get("channel", "EMAIL"),
            subject=parameters.get("subject", "Automated Workflow Notification"),
            template_id=parameters.get("template_id", "default-template"),
            payload=cloned_payload,
            priority=parameters.get("priority", "NORMAL"),
            idempotency_key=f"wf-exec-{execution_context['execution_id']}-SEND_NOTIFICATION-{execution_context.get('action_index', 0)}",
            source_event_id=execution_context["source_event_id"],
            source_event_type=execution_context["source_event_type"]
        )

        repo = PostgresNotificationRepository(session)
        handler = CreateNotificationHandler(repo)
        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            raise ValueError(f"SEND_NOTIFICATION action failed: {res.error.message}")

        return {"notification_id": notif_id, "status": "success"}


class CreateSettlementActionHandler(IWorkflowActionHandler):
    """
    Workflow action handler resolving and executing CREATE_SETTLEMENT.
    """
    async def execute(
        self,
        parameters: Dict[str, Any],
        session: AsyncSession,
        execution_context: Dict[str, Any],
        payload: Dict[str, Any],
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        payment_id = parameters.get("payment_id") or payload.get("payment_id") or payload.get("id")
        if not payment_id:
            raise ValueError("CREATE_SETTLEMENT action failed: payment_id not found in action or payload context.")

        settlement_id = str(uuid4())
        cmd = CreateSettlementCommand(
            settlement_id=settlement_id,
            organization_id=execution_context["organization_id"],
            payment_id=payment_id,
            amount=Decimal(parameters.get("amount", payload.get("observed_amount", payload.get("amount", "0.00")))),
            asset_code=parameters.get("asset_code", payload.get("asset_code", "USDC")),
            asset_type="DIGITAL_ASSET",
            asset_decimals=int(parameters.get("asset_decimals", 6)),
            network=parameters.get("network", payload.get("network_code", "BASE")),
            destination_type="BLOCKCHAIN_ADDRESS",
            destination_identifier=parameters.get("destination_identifier", payload.get("receiver", "0xDefaultReceiver")),
            method="BLOCKCHAIN",
            reference=f"SETL-{str(uuid4())[:8]}",
            idempotency_key=f"wf-exec-{execution_context['execution_id']}-CREATE_SETTLEMENT-{execution_context.get('action_index', 0)}"
        )

        settlement_repo = PostgresSettlementRepository(session)
        payment_repo = PostgresPaymentRepository(session)
        availability_svc = SettlementAvailabilityService(settlement_repo, payment_repo)
        handler = CreateSettlementHandler(settlement_repo, payment_repo, availability_svc)

        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            raise ValueError(f"CREATE_SETTLEMENT action failed: {res.error.message}")

        return {"settlement_id": settlement_id, "status": "success"}


class WorkflowActionRegistry:
    """
    Registry for dynamic workflow action mapping and execution.
    """
    def __init__(self):
        self._handlers: Dict[str, IWorkflowActionHandler] = {}
        # Auto-register core actions
        self.register("SEND_NOTIFICATION", SendNotificationActionHandler())
        self.register("CREATE_SETTLEMENT", CreateSettlementActionHandler())

    def register(self, action_type: str, handler: IWorkflowActionHandler) -> None:
        self._handlers[action_type.upper()] = handler

    def get(self, action_type: str) -> IWorkflowActionHandler:
        handler = self._handlers.get(action_type.upper())
        if not handler:
            raise ValueError(f"Unsupported action type: {action_type}")
        return handler
