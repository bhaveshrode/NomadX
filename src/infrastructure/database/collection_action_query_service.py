from typing import List, Dict, Any, Optional
from sqlalchemy import select, func, cast, Numeric, case
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal

from ...domain.organization.value_objects import OrganizationId
from ...application.receivable.collection_action_query_service import ICollectionActionQueryService
from .models import ReceivableCollectionActionDbModel, ReceivableDbModel


class PostgresCollectionActionQueryService(ICollectionActionQueryService):
    """
    SQLAlchemy/PostgreSQL implementation of ICollectionActionQueryService.
    Provides fast, read-only aggregations for collection action observability.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_failed_actions(
        self,
        organization_id: OrganizationId,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(ReceivableCollectionActionDbModel)
            .where(
                ReceivableCollectionActionDbModel.organization_id == organization_id.value,
                ReceivableCollectionActionDbModel.status.in_(["FAILED", "RETRYING"])
            )
            .order_by(ReceivableCollectionActionDbModel.scheduled_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        actions = result.scalars().all()
        return [self._to_dict(a) for a in actions]

    async def get_dead_letter_actions(
        self,
        organization_id: OrganizationId,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(ReceivableCollectionActionDbModel)
            .where(
                ReceivableCollectionActionDbModel.organization_id == organization_id.value,
                ReceivableCollectionActionDbModel.status == "DEAD_LETTERED"
            )
            .order_by(ReceivableCollectionActionDbModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        actions = result.scalars().all()
        return [self._to_dict(a) for a in actions]

    async def get_collection_action_metrics(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        # Perform single query grouping status aggregates
        stmt = select(
            ReceivableCollectionActionDbModel.status,
            func.count(ReceivableCollectionActionDbModel.id),
            func.sum(ReceivableCollectionActionDbModel.attempt_count)
        ).where(
            ReceivableCollectionActionDbModel.organization_id == organization_id.value
        ).group_by(
            ReceivableCollectionActionDbModel.status
        )

        res = await self._session.execute(stmt)
        rows = res.all()

        counts = {
            "PENDING": 0,
            "PROCESSING": 0,
            "COMPLETED": 0,
            "FAILED": 0,
            "RETRYING": 0,
            "DEAD_LETTERED": 0,
            "CANCELLED": 0
        }
        total_attempts = 0

        for status, cnt, attempts in rows:
            if status in counts:
                counts[status] = cnt
            else:
                counts[status] = counts.get(status, 0) + cnt
            total_attempts += int(attempts or 0)

        # Calculate success rate: completed / (completed + dead_letter)
        completed = counts["COMPLETED"]
        dead_letter = counts["DEAD_LETTERED"]
        total_finished = completed + dead_letter
        success_rate = 100.0 if total_finished == 0 else float(completed) / total_finished * 100.0

        # Query receivables requiring human attention (DLQ actions exists or status is WRITTEN_OFF)
        dlq_receivables_stmt = select(func.count(func.distinct(ReceivableCollectionActionDbModel.receivable_id))).where(
            ReceivableCollectionActionDbModel.organization_id == organization_id.value,
            ReceivableCollectionActionDbModel.status == "DEAD_LETTERED"
        )
        res_dlq_recs = await self._session.execute(dlq_receivables_stmt)
        recs_requiring_attention = res_dlq_recs.scalar() or 0

        return {
            "pending_actions": counts["PENDING"],
            "processing_actions": counts["PROCESSING"],
            "completed_actions": counts["COMPLETED"],
            "failed_actions": counts["FAILED"],
            "retrying_actions": counts["RETRYING"],
            "dead_lettered_actions": counts["DEAD_LETTERED"],
            "cancelled_actions": counts["CANCELLED"],
            "retry_count": total_attempts,
            "success_rate": f"{success_rate:.1f}%",
            "receivables_requiring_attention": recs_requiring_attention
        }

    def _to_dict(self, model: ReceivableCollectionActionDbModel) -> Dict[str, Any]:
        return {
            "id": model.id,
            "organization_id": model.organization_id,
            "receivable_id": model.receivable_id,
            "action_type": model.action_type,
            "scheduled_at": model.scheduled_at.isoformat() if model.scheduled_at else None,
            "status": model.status,
            "attempt_count": model.attempt_count,
            "max_attempts": model.max_attempts,
            "next_retry_at": model.next_retry_at.isoformat() if model.next_retry_at else None,
            "idempotency_key": model.idempotency_key,
            "created_at": model.created_at.isoformat() if model.created_at else None,
            "processed_at": model.processed_at.isoformat() if model.processed_at else None,
            "dead_lettered_at": model.dead_lettered_at.isoformat() if model.dead_lettered_at else None,
            "failure_classification": model.failure_classification,
            "error_message": model.error_message
        }
