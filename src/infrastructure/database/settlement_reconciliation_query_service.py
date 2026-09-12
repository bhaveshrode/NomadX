from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
import json

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.organization.value_objects import OrganizationId
from ...application.settlement_reconciliation.query_service import ISettlementReconciliationQueryService
from .models import (
    SettlementReconciliationDbModel,
    SettlementEntryDbModel,
    SettlementAdjustmentDbModel,
    SettlementDiscrepancyDbModel,
    OutboxEventDbModel
)


def _map_settlement(model: SettlementReconciliationDbModel) -> Dict[str, Any]:
    # Calculate amounts
    gross_total = sum(Decimal(entry.gross_amount) for entry in model.entries)
    fees_total = sum(Decimal(entry.fees_amount) for entry in model.entries)
    entry_adjs = sum(Decimal(entry.adjustments_amount) for entry in model.entries)
    agg_adjs = sum(Decimal(adj.amount) for adj in model.adjustments)
    adjustments_total = entry_adjs + agg_adjs
    net_total = gross_total - fees_total - adjustments_total

    # Format reconciliation result
    rec_result = None
    if model.rec_verified_at is not None:
        discrepancies = [
            {
                "entry_reference": d.entry_reference,
                "discrepancy_type": d.discrepancy_type,
                "expected_amount": d.expected_amount,
                "actual_amount": d.actual_amount,
                "difference_amount": d.difference_amount,
                "description": d.description,
                "resolved": d.resolved
            }
            for d in model.discrepancies
        ]
        rec_result = {
            "status": model.reconciliation_state,
            "verified_at": model.rec_verified_at.isoformat() if model.rec_verified_at else None,
            "description": model.rec_description or "",
            "discrepancies": discrepancies
        }

    return {
        "id": model.id,
        "organization_id": model.organization_id,
        "reference": model.reference,
        "merchant_reference": model.merchant_reference,
        "currency": model.currency_code,
        "period_start": model.period_start.isoformat(),
        "period_end": model.period_end.isoformat(),
        "status": model.status,
        "reconciliation_status": model.reconciliation_state,
        "gross_amount": f"{gross_total:.2f}",
        "fees": f"{fees_total:.2f}",
        "adjustments": f"{adjustments_total:.2f}",
        "net_amount": f"{net_total:.2f}",
        "idempotency_key": model.idempotency_key,
        "created_at": model.created_at.isoformat(),
        "updated_at": model.updated_at.isoformat(),
        "reconciliation_result": rec_result,
        "processing_state": model.processing_state,
        "attempt_count": model.attempt_count,
        "max_attempts": model.max_attempts,
        "last_error": model.last_error,
        "failure_classification": model.failure_classification,
        "next_retry_at": model.next_retry_at.isoformat() if model.next_retry_at else None,
        "processing_started_at": model.processing_started_at.isoformat() if model.processing_started_at else None,
        "processing_completed_at": model.processing_completed_at.isoformat() if model.processing_completed_at else None,
        "dead_lettered_at": model.dead_lettered_at.isoformat() if model.dead_lettered_at else None,
        "paused": model.paused
    }


class PostgresSettlementQueryService(ISettlementReconciliationQueryService):
    """
    SQLAlchemy-backed implementation of read-side queries for Settlement & Reconciliation.
    Enforces strict tenant boundary isolation checks for all queries.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_settlement(self, organization_id: OrganizationId, settlement_id: str) -> Optional[Dict[str, Any]]:
        stmt = (
            select(SettlementReconciliationDbModel)
            .where(
                SettlementReconciliationDbModel.organization_id == organization_id.value,
                SettlementReconciliationDbModel.id == settlement_id
            )
            .options(
                selectinload(SettlementReconciliationDbModel.entries),
                selectinload(SettlementReconciliationDbModel.adjustments),
                selectinload(SettlementReconciliationDbModel.discrepancies)
            )
        )
        res = await self._session.execute(stmt)
        model = res.scalar_one_or_none()
        return _map_settlement(model) if model else None

    async def list_settlements(
        self,
        organization_id: OrganizationId,
        reference: Optional[str] = None,
        status: Optional[str] = None,
        currency: Optional[str] = None,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
        merchant_reference: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(SettlementReconciliationDbModel)
            .where(SettlementReconciliationDbModel.organization_id == organization_id.value)
            .options(
                selectinload(SettlementReconciliationDbModel.entries),
                selectinload(SettlementReconciliationDbModel.adjustments),
                selectinload(SettlementReconciliationDbModel.discrepancies)
            )
        )
        if reference:
            stmt = stmt.where(SettlementReconciliationDbModel.reference == reference)
        if status:
            stmt = stmt.where(SettlementReconciliationDbModel.status == status)
        if currency:
            stmt = stmt.where(SettlementReconciliationDbModel.currency_code == currency)
        if period_start:
            stmt = stmt.where(SettlementReconciliationDbModel.period_start >= period_start)
        if period_end:
            stmt = stmt.where(SettlementReconciliationDbModel.period_end <= period_end)
        if merchant_reference:
            stmt = stmt.where(SettlementReconciliationDbModel.merchant_reference == merchant_reference)

        sort_col = getattr(SettlementReconciliationDbModel, sort_by) if sort_by and hasattr(SettlementReconciliationDbModel, sort_by) else SettlementReconciliationDbModel.created_at
        if sort_order == "desc":
            stmt = stmt.order_by(sort_col.desc())
        else:
            stmt = stmt.order_by(sort_col.asc())

        stmt = stmt.limit(limit).offset(offset)
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        return [_map_settlement(m) for m in models]

    async def get_settlement_entries(self, organization_id: OrganizationId, settlement_id: str) -> List[Dict[str, Any]]:
        stmt = (
            select(SettlementEntryDbModel)
            .join(SettlementReconciliationDbModel)
            .where(
                SettlementReconciliationDbModel.organization_id == organization_id.value,
                SettlementEntryDbModel.settlement_id == settlement_id
            )
        )
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        return [
            {
                "id": m.id,
                "settlement_id": m.settlement_id,
                "source_transaction_reference": m.source_transaction_reference,
                "source_context_type": m.source_context_type,
                "gross_amount": m.gross_amount,
                "fees_amount": m.fees_amount,
                "adjustments_amount": m.adjustments_amount,
                "net_amount": m.net_amount,
                "currency": m.currency_code,
                "reconciliation_state": m.reconciliation_state,
                "status": m.status
            }
            for m in models
        ]

    async def get_settlement_adjustments(self, organization_id: OrganizationId, settlement_id: str) -> List[Dict[str, Any]]:
        stmt = (
            select(SettlementAdjustmentDbModel)
            .join(SettlementReconciliationDbModel)
            .where(
                SettlementReconciliationDbModel.organization_id == organization_id.value,
                SettlementAdjustmentDbModel.settlement_id == settlement_id
            )
        )
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        return [
            {
                "id": m.id,
                "settlement_id": m.settlement_id,
                "type": m.type,
                "description": m.description,
                "amount": m.amount,
                "currency": m.currency_code
            }
            for m in models
        ]

    async def get_settlement_discrepancies(self, organization_id: OrganizationId, settlement_id: str) -> List[Dict[str, Any]]:
        stmt = (
            select(SettlementDiscrepancyDbModel)
            .join(SettlementReconciliationDbModel)
            .where(
                SettlementReconciliationDbModel.organization_id == organization_id.value,
                SettlementDiscrepancyDbModel.settlement_id == settlement_id
            )
        )
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        return [
            {
                "id": m.id,
                "settlement_id": m.settlement_id,
                "entry_reference": m.entry_reference,
                "discrepancy_type": m.discrepancy_type,
                "expected_amount": m.expected_amount,
                "actual_amount": m.actual_amount,
                "difference_amount": m.difference_amount,
                "description": m.description,
                "resolved": m.resolved
            }
            for m in models
        ]

    async def get_reconciliation_result(self, organization_id: OrganizationId, settlement_id: str) -> Optional[Dict[str, Any]]:
        settlement = await self.get_settlement(organization_id, settlement_id)
        return settlement["reconciliation_result"] if settlement else None

    async def list_failed_settlements(self, organization_id: OrganizationId, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        stmt = (
            select(SettlementReconciliationDbModel)
            .where(
                SettlementReconciliationDbModel.organization_id == organization_id.value,
                SettlementReconciliationDbModel.processing_state.in_(["FAILED", "DEAD_LETTERED"])
            )
            .options(
                selectinload(SettlementReconciliationDbModel.entries),
                selectinload(SettlementReconciliationDbModel.adjustments),
                selectinload(SettlementReconciliationDbModel.discrepancies)
            )
            .limit(limit)
            .offset(offset)
        )
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        return [_map_settlement(m) for m in models]

    async def list_dead_letter_settlements(self, organization_id: OrganizationId, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        stmt = (
            select(SettlementReconciliationDbModel)
            .where(
                SettlementReconciliationDbModel.organization_id == organization_id.value,
                SettlementReconciliationDbModel.processing_state == "DEAD_LETTERED"
            )
            .options(
                selectinload(SettlementReconciliationDbModel.entries),
                selectinload(SettlementReconciliationDbModel.adjustments),
                selectinload(SettlementReconciliationDbModel.discrepancies)
            )
            .limit(limit)
            .offset(offset)
        )
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        return [_map_settlement(m) for m in models]

    async def get_settlement_metrics(self, organization_id: OrganizationId) -> Dict[str, Any]:
        stmt = (
            select(SettlementReconciliationDbModel)
            .where(SettlementReconciliationDbModel.organization_id == organization_id.value)
            .options(
                selectinload(SettlementReconciliationDbModel.entries),
                selectinload(SettlementReconciliationDbModel.adjustments),
                selectinload(SettlementReconciliationDbModel.discrepancies)
            )
        )
        res = await self._session.execute(stmt)
        models = res.scalars().all()

        total = len(models)
        pending = sum(1 for m in models if m.processing_state == "PENDING")
        processing = sum(1 for m in models if m.processing_state == "PROCESSING")
        successful = sum(1 for m in models if m.reconciliation_state == "MATCHED")
        failed = sum(1 for m in models if m.processing_state in ("FAILED", "DEAD_LETTERED"))
        dead_letter = sum(1 for m in models if m.processing_state == "DEAD_LETTERED")
        total_retries = sum(m.attempt_count for m in models)
        discrepancy_count = sum(len(m.discrepancies) for m in models)

        # Average duration of completed reconciliations
        durations = []
        for m in models:
            if m.processing_completed_at and m.processing_started_at:
                durations.append((m.processing_completed_at - m.processing_started_at).total_seconds())
        avg_duration = sum(durations) / len(durations) if durations else 0.0

        match_rate = successful / total if total > 0 else 1.0
        failure_rate = failed / total if total > 0 else 0.0

        return {
            "total_settlements": total,
            "pending_settlements": pending,
            "processing_settlements": processing,
            "successful_reconciliations": successful,
            "failed_reconciliations": failed,
            "dead_lettered_executions": dead_letter,
            "retry_counts": total_retries,
            "discrepancy_counts": discrepancy_count,
            "average_reconciliation_duration": avg_duration,
            "match_rate": match_rate,
            "failure_rate": failure_rate
        }

    async def get_settlement_audit_logs(self, organization_id: OrganizationId, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        event_types = (
            "SettlementCreated",
            "SettlementOpened",
            "SettlementEntryAdded",
            "SettlementReconciliationStarted",
            "SettlementReconciled",
            "SettlementDiscrepancyDetected",
            "SettlementCompleted",
            "SettlementCancelled",
            "SettlementAdjustmentApplied",
            "SettlementProcessingRetried",
            "SettlementProcessingReplayed",
            "SettlementProcessingCancelled",
            "SettlementProcessingDeadLettered",
            "SettlementProcessingRecovered",
            "SettlementProcessingPaused",
            "SettlementProcessingResumed",
            "ReconciliationRetryScheduled",
            "ReconciliationDiscrepancyResolved",
            "SettlementProcessingFailed"
        )
        
        stmt = (
            select(OutboxEventDbModel)
            .where(
                OutboxEventDbModel.event_type.in_(event_types),
                OutboxEventDbModel.payload.like(f'%"organization_id": "{organization_id.value}"%')
            )
            .order_by(OutboxEventDbModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        res = await self._session.execute(stmt)
        events = res.scalars().all()
        
        audit_trail = []
        for e in events:
            try:
                payload = json.loads(e.payload)
            except Exception:
                payload = {}
            metadata = payload.get("_metadata", {})
            audit_trail.append({
                "id": e.id,
                "event_type": e.event_type,
                "occurred_on": e.created_at.isoformat(),
                "correlation_id": metadata.get("correlation_id") or payload.get("correlation_id") or e.id,
                "causation_id": metadata.get("causation_id") or e.id,
                "settlement_id": payload.get("settlement_id"),
                "details": payload
            })
        return audit_trail

    async def get_settlement_timeline(self, organization_id: OrganizationId, settlement_id: str) -> List[Dict[str, Any]]:
        chk = await self.get_settlement(organization_id, settlement_id)
        if not chk:
            return []
            
        stmt = (
            select(OutboxEventDbModel)
            .where(
                OutboxEventDbModel.payload.like(f'%"settlement_id": "{settlement_id}"%')
            )
            .order_by(OutboxEventDbModel.created_at.asc())
        )
        res = await self._session.execute(stmt)
        events = res.scalars().all()
        
        timeline = []
        for e in events:
            try:
                payload = json.loads(e.payload)
            except Exception:
                payload = {}
            metadata = payload.get("_metadata", {})
            timeline.append({
                "event_type": e.event_type,
                "occurred_on": e.created_at.isoformat(),
                "correlation_id": metadata.get("correlation_id") or e.id,
                "causation_id": metadata.get("causation_id") or e.id,
                "details": payload
            })
        return timeline

    async def get_settlement_health(self, organization_id: OrganizationId) -> Dict[str, Any]:
        stmt = (
            select(SettlementReconciliationDbModel)
            .where(SettlementReconciliationDbModel.organization_id == organization_id.value)
            .options(
                selectinload(SettlementReconciliationDbModel.entries),
                selectinload(SettlementReconciliationDbModel.adjustments),
                selectinload(SettlementReconciliationDbModel.discrepancies)
            )
        )
        res = await self._session.execute(stmt)
        models = res.scalars().all()
        
        from datetime import datetime, timedelta
        cutoff_stuck = datetime.utcnow() - timedelta(minutes=15)
        stuck_jobs = sum(1 for m in models if m.processing_state == "PROCESSING" and m.processing_started_at and m.processing_started_at < cutoff_stuck)
        
        retry_queue = sum(1 for m in models if m.processing_state == "RETRYING")
        dlq_size = sum(1 for m in models if m.processing_state == "DEAD_LETTERED")
        unresolved = sum(sum(1 for d in m.discrepancies if not d.resolved) for m in models)
        
        return {
            "stuck_processing_jobs": stuck_jobs,
            "retry_queue_size": retry_queue,
            "dead_letter_queue_size": dlq_size,
            "unresolved_discrepancies": unresolved,
            "status": "HEALTHY" if (stuck_jobs == 0 and dlq_size < 5) else "WARNING"
        }
