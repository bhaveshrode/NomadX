from datetime import datetime, timezone
from typing import Dict, Any, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.session import session_manager
from ..database.models import OrganizationDbModel, FinancialConsistencyViolationDbModel
from ...domain.organization.value_objects import OrganizationId
from ...presentation.api.dependencies.context import ExecutionContext
from ...application.financial_consistency.query_service import IFinancialConsistencyQueryService
from ...application.financial_recovery.service import IFinancialRecoveryService
from ...infrastructure.database.financial_consistency_query_service import PostgresFinancialConsistencyQueryService
from ...infrastructure.database.financial_recovery_service import PostgresFinancialRecoveryService


class FinancialConsistencyAutomationWorker:
    """
    Background worker that runs automatically to discover organizations,
    recovers stuck processing states, replays outbox events, and executes
    cross-context financial invariant checks continuously.
    """
    def __init__(
        self,
        consistency_service: IFinancialConsistencyQueryService,
        recovery_service: IFinancialRecoveryService
    ):
        self._consistency_service = consistency_service
        self._recovery_service = recovery_service

    async def run_integrity_sweep(self) -> Dict[str, Any]:
        """
        Executes a system-wide continuous reconciliation sweep across all organizations.
        """
        # 1. Discover all registered organizations
        async with session_manager.session() as session:
            stmt = select(OrganizationDbModel.id)
            res = await session.execute(stmt)
            org_ids = res.scalars().all()

        organizations_swept = 0
        total_active_violations = 0
        total_resolved_violations = 0
        events_recovered = 0
        settlements_recovered = 0

        for org_id in org_ids:
            # Create a system execution context for this organization
            ctx = ExecutionContext(
                user_id="financial_consistency_automation_worker",
                organization_id=org_id,
                role="SYSTEM"
            )
            organization_id = OrganizationId(org_id)

            try:
                # 2. Recover stuck outbox events and reconciliation batches
                recover_res = await self._recovery_service.recover_stuck_processing(organization_id, ctx)
                events_recovered += recover_res.get("events_recovered", 0)
                settlements_recovered += recover_res.get("settlements_recovered", 0)

                # 3. Perform consistency checks (which auto-resolve and persist violations)
                report = await self._recovery_service.recheck_consistency(organization_id, ctx)
                
                # Fetch active / resolved counts for this org
                metrics = await self._consistency_service.get_consistency_metrics(organization_id)
                total_active_violations += metrics.get("active_violations", 0)
                total_resolved_violations += metrics.get("resolved_violations", 0)
                
                organizations_swept += 1
            except Exception as e:
                print(f"[Automation Worker] Error sweeping organization {org_id}: {str(e)}")

        return {
            "status": "COMPLETED",
            "run_at": datetime.now(timezone.utc).isoformat(),
            "organizations_swept": organizations_swept,
            "total_active_violations": total_active_violations,
            "total_resolved_violations": total_resolved_violations,
            "events_recovered": events_recovered,
            "settlements_recovered": settlements_recovered
        }
