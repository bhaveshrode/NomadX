from datetime import datetime, timezone
import json
from uuid import uuid4
from typing import Dict, Any, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.organization.value_objects import OrganizationId
from ...presentation.api.dependencies.context import ExecutionContext
from ...application.financial_recovery.service import IFinancialRecoveryService
from ...application.financial_consistency.query_service import IFinancialConsistencyQueryService
from .models import (
    OutboxEventDbModel,
    FinancialRemediationAuditDbModel,
    SettlementReconciliationDbModel,
    SettlementDbModel
)
from ..workers.ar_event_processor import AREventProcessor
from ...application.settlement_reconciliation.automation_service import ReconciliationAutomationService
from .settlement_reconciliation_repository import PostgresSettlementRepository
from .source_transaction_repository import PostgresSourceTransactionRepository


class PostgresFinancialRecoveryService(IFinancialRecoveryService):
    """
    SQLAlchemy-backed implementation of IFinancialRecoveryService.
    Coordinates controlled remediation actions and records them in the audit log.
    """
    def __init__(
        self,
        session: AsyncSession,
        consistency_service: IFinancialConsistencyQueryService
    ):
        self._session = session
        self._consistency_service = consistency_service

    async def _audit(
        self,
        organization_id: OrganizationId,
        action_type: str,
        target_entity_id: str | None,
        previous_state: str | None,
        resulting_state: str | None,
        status: str,
        error_message: str | None,
        ctx: ExecutionContext
    ) -> None:
        # Create an audit log record
        audit = FinancialRemediationAuditDbModel(
            id=str(uuid4()),
            organization_id=organization_id.value,
            action_type=action_type,
            target_entity_id=target_entity_id,
            previous_state=previous_state,
            resulting_state=resulting_state,
            status=status,
            error_message=error_message,
            correlation_id=getattr(ctx, "correlation_id", None) or str(uuid4()),
            causation_id=getattr(ctx, "user_id", "SYSTEM"),
            operator_id=ctx.user_id,
            performed_at=datetime.utcnow()
        )
        self._session.add(audit)
        await self._session.flush()

    async def recheck_consistency(
        self,
        organization_id: OrganizationId,
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        try:
            # Query current health before check
            metrics_before = await self._consistency_service.get_consistency_metrics(organization_id)
            prev_state = metrics_before["status"]

            # Run check (updates DB findings)
            report = await self._consistency_service.check_financial_consistency(organization_id)
            res_state = report["status"]

            await self._audit(
                organization_id=organization_id,
                action_type="RECHECK",
                target_entity_id=None,
                previous_state=prev_state,
                resulting_state=res_state,
                status="SUCCESS",
                error_message=None,
                ctx=ctx
            )
            return report
        except Exception as e:
            await self._audit(
                organization_id=organization_id,
                action_type="RECHECK",
                target_entity_id=None,
                previous_state=None,
                resulting_state=None,
                status="FAILED",
                error_message=str(e),
                ctx=ctx
            )
            raise e

    async def retry_failed_event(
        self,
        organization_id: OrganizationId,
        event_id: str,
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        # Fetch outbox event
        stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.id == event_id)
        res = await self._session.execute(stmt)
        event = res.scalar_one_or_none()
        if not event:
            return {"status": "NOT_FOUND", "error_message": "Event not found."}

        # Parse payload and verify tenant isolation
        try:
            payload = json.loads(event.payload)
        except Exception:
            payload = {}
        ev_org_id = payload.get("organization_id") or payload.get("_metadata", {}).get("organization_id")
        if ev_org_id and ev_org_id != organization_id.value:
            return {"status": "NOT_FOUND", "error_message": "Event not found."}

        previous_state = f"{event.status} (attempts={event.attempts})"
        
        try:
            # Reset event status to pending
            event.status = "PENDING"
            event.processed = False
            event.attempts = 0
            event.next_retry_at = None
            event.error_message = None
            await self._session.commit()

            # Execute event processor immediately to fulfill controlled retry
            ar_processor = AREventProcessor()
            await ar_processor.process_payment_events()

            resulting_state = "PROCESSED"
            
            # Check consistency to refresh metrics
            await self._consistency_service.check_financial_consistency(organization_id)

            await self._audit(
                organization_id=organization_id,
                action_type="RETRY_EVENT",
                target_entity_id=event_id,
                previous_state=previous_state,
                resulting_state=resulting_state,
                status="SUCCESS",
                error_message=None,
                ctx=ctx
            )
            return {"status": "SUCCESS", "message": f"Event {event_id} retried and processed."}
        except Exception as e:
            await self._audit(
                organization_id=organization_id,
                action_type="RETRY_EVENT",
                target_entity_id=event_id,
                previous_state=previous_state,
                resulting_state="FAILED",
                status="FAILED",
                error_message=str(e),
                ctx=ctx
            )
            return {"status": "FAILED", "error_message": str(e)}

    async def replay_event(
        self,
        organization_id: OrganizationId,
        event_id: str,
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        # Fetch event
        stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.id == event_id)
        res = await self._session.execute(stmt)
        event = res.scalar_one_or_none()
        if not event:
            return {"status": "NOT_FOUND", "error_message": "Event not found."}

        # Verify tenant
        try:
            payload = json.loads(event.payload)
        except Exception:
            payload = {}
        ev_org_id = payload.get("organization_id") or payload.get("_metadata", {}).get("organization_id")
        if ev_org_id and ev_org_id != organization_id.value:
            return {"status": "NOT_FOUND", "error_message": "Event not found."}

        # Safety Check: Cannot mutate finalized resources (like completed settlements)
        if event.event_type == "SettlementCompleted":
            # Check if settlement is already completed
            settlement_id = payload.get("settlement_id")
            if settlement_id:
                stmt_set = select(SettlementReconciliationDbModel).where(SettlementReconciliationDbModel.id == settlement_id)
                res_set = await self._session.execute(stmt_set)
                settlement = res_set.scalar_one_or_none()
                if settlement and settlement.status == "COMPLETED":
                    # Record escalation / block
                    await self._audit(
                        organization_id=organization_id,
                        action_type="REPLAY_EVENT",
                        target_entity_id=event_id,
                        previous_state="COMPLETED",
                        resulting_state="COMPLETED",
                        status="ESCALATED",
                        error_message="Event replay blocked: target Settlement is already finalized and completed.",
                        ctx=ctx
                    )
                    return {
                        "status": "ESCALATED",
                        "error_message": "Replay blocked: Settlement is already finalized and completed."
                    }

        previous_state = f"{event.status} (processed={event.processed})"

        try:
            # Force replay: reset event back to PENDING
            event.status = "PENDING"
            event.processed = False
            event.attempts = 0
            event.next_retry_at = None
            event.error_message = None
            await self._session.commit()

            # Execute event processor immediately
            ar_processor = AREventProcessor()
            await ar_processor.process_payment_events()

            resulting_state = "REPLAYED"

            # Recheck consistency
            await self._consistency_service.check_financial_consistency(organization_id)

            await self._audit(
                organization_id=organization_id,
                action_type="REPLAY_EVENT",
                target_entity_id=event_id,
                previous_state=previous_state,
                resulting_state=resulting_state,
                status="SUCCESS",
                error_message=None,
                ctx=ctx
            )
            return {"status": "SUCCESS", "message": f"Event {event_id} replayed successfully."}
        except Exception as e:
            await self._audit(
                organization_id=organization_id,
                action_type="REPLAY_EVENT",
                target_entity_id=event_id,
                previous_state=previous_state,
                resulting_state="FAILED",
                status="FAILED",
                error_message=str(e),
                ctx=ctx
            )
            return {"status": "FAILED", "error_message": str(e)}

    async def recover_stuck_processing(
        self,
        organization_id: OrganizationId,
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        try:
            # 1. Recover stuck outbox events
            ar_processor = AREventProcessor()
            stuck_events_recovered = await ar_processor.recover_stuck_events(threshold_minutes=0)

            # 2. Recover stuck settlement batches
            settlement_repo = PostgresSettlementRepository(self._session)
            source_tx_repo = PostgresSourceTransactionRepository(self._session)
            automation_service = ReconciliationAutomationService(settlement_repo, source_tx_repo)
            stuck_settlements_recovered = await automation_service.recover_stuck_settlements(timeout_minutes=0)

            # Rerun check
            await self._consistency_service.check_financial_consistency(organization_id)

            resulting_state = f"Recovered: {stuck_events_recovered} events, {stuck_settlements_recovered} settlements"

            await self._audit(
                organization_id=organization_id,
                action_type="RECOVER_STUCK",
                target_entity_id=None,
                previous_state="STUCK",
                resulting_state=resulting_state,
                status="SUCCESS",
                error_message=None,
                ctx=ctx
            )
            return {
                "status": "SUCCESS",
                "events_recovered": stuck_events_recovered,
                "settlements_recovered": stuck_settlements_recovered
            }
        except Exception as e:
            await self._audit(
                organization_id=organization_id,
                action_type="RECOVER_STUCK",
                target_entity_id=None,
                previous_state="STUCK",
                resulting_state="FAILED",
                status="FAILED",
                error_message=str(e),
                ctx=ctx
            )
            raise e

    async def get_remediation_history(
        self,
        organization_id: OrganizationId
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(FinancialRemediationAuditDbModel)
            .where(FinancialRemediationAuditDbModel.organization_id == organization_id.value)
            .order_by(FinancialRemediationAuditDbModel.performed_at.desc())
        )
        res = await self._session.execute(stmt)
        audits = res.scalars().all()
        return [
            {
                "id": a.id,
                "organization_id": a.organization_id,
                "action_type": a.action_type,
                "target_entity_id": a.target_entity_id,
                "previous_state": a.previous_state,
                "resulting_state": a.resulting_state,
                "status": a.status,
                "error_message": a.error_message,
                "correlation_id": a.correlation_id,
                "causation_id": a.causation_id,
                "operator_id": a.operator_id,
                "performed_at": a.performed_at.isoformat() if a.performed_at else None
            }
            for a in audits
        ]
